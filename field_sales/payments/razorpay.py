# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Razorpay REST API (payment links for Field Orders).

Credentials live in the Razorpay Settings Single; the secrets are read only
with ``get_password()`` on the server and never returned to the client.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import cint, escape_html, flt, get_datetime, get_system_timezone, now_datetime

from field_sales.field_sales.doctype.sync_log.sync_log import log_sync, log_sync_durable

API_BASE = "https://api.razorpay.com/v1"
REQUEST_TIMEOUT = 20  # seconds
WEBHOOK_PATH = "/api/method/field_sales.payments.razorpay.webhook"
SETTINGS_ROLES = ("System Manager", "Sales Manager")
PLATFORM = "Razorpay"  # Sync Log platform
MIN_EXPIRY_SECONDS = 16 * 60  # Razorpay wants expire_by at least 15 minutes ahead
MAX_REFERENCE_TRIES = 10
# Razorpay link states that can still be paid, so must be cancelled before a new link.
OPEN_LINK_STATES = ("created", "partially_paid")


class RazorpayError(Exception):
	"""A failed Razorpay call; ``message`` is Razorpay's ``error.description`` when it sent one."""

	def __init__(self, message: str, http_status: int | None = None, code: str | None = None):
		super().__init__(message)
		self.message = message
		self.http_status = http_status
		self.code = code


def get_settings():
	return frappe.get_single("Razorpay Settings")


def _credentials(settings) -> tuple[str, str]:
	key_id = (settings.key_id or "").strip()
	key_secret = settings.get_password("key_secret", raise_exception=False)
	if not key_id or not key_secret:
		raise RazorpayError(_("Razorpay Key ID and Key Secret are not set"), code="NOT_CONFIGURED")
	return key_id, key_secret


def request(
	method: str, path: str, *, params: dict | None = None, json: dict | None = None, settings=None
) -> dict:
	"""
	Call the Razorpay API with basic auth and return the JSON body.

	:raises RazorpayError: no credentials, network failure, or a 4xx/5xx answer.
	"""
	auth = _credentials(settings or get_settings())
	try:
		response = requests.request(
			method, f"{API_BASE}{path}", params=params, json=json, auth=auth, timeout=REQUEST_TIMEOUT
		)
	except requests.RequestException as e:
		raise RazorpayError(
			_("Could not reach Razorpay: {0}").format(type(e).__name__), code="NETWORK_ERROR"
		) from e

	try:
		body = response.json()
	except ValueError:
		body = {}
	if not isinstance(body, dict):
		body = {}

	if response.status_code >= 400:
		error = body.get("error") or {}
		raise RazorpayError(
			error.get("description") or _("Razorpay returned HTTP {0}").format(response.status_code),
			http_status=response.status_code,
			code=error.get("code"),
		)
	return body


@frappe.whitelist(methods=["GET"])
def get_webhook_url() -> str:
	"""The URL to enter in the Razorpay Dashboard (the SPA loads settings without ``onload``)."""
	frappe.only_for(SETTINGS_ROLES)
	return frappe.utils.get_url(WEBHOOK_PATH)


@frappe.whitelist(methods=["POST"])
def test_connection() -> dict:
	"""
	Check the saved keys by listing one payment link.

	:return: ``{"ok": bool, "message": str}``; ``message`` is Razorpay's error on failure.
	"""
	frappe.only_for(SETTINGS_ROLES)

	settings = get_settings()
	try:
		request("GET", "/payment_links", params={"count": 1}, settings=settings)
	except RazorpayError as e:
		return {"ok": False, "message": e.message}
	return {"ok": True, "message": _("Connected to Razorpay ({0} mode)").format(settings.mode or "Test")}


# ----------------------------------------------------------------------
# Payment link client
# ----------------------------------------------------------------------


def create_payment_link(doc, reference_id: str | None = None, settings=None, notify: bool = True) -> dict:
	"""
	Create a Razorpay payment link for a Field Order's grand total and return Razorpay's link object.

	:param reference_id: Unique per link on Razorpay; defaults to the order name.
	:param notify: False stops Razorpay's own SMS/email whatever the settings say.
	"""
	settings = settings or get_settings()
	payload = _link_payload(doc, reference_id or doc.name, settings, notify=notify)
	return _logged(
		"Create Payment Link",
		doc.name,
		payload,
		lambda: request("POST", "/payment_links", json=payload, settings=settings),
	)


def fetch_payment_link(link_id: str, settings=None) -> dict:
	return request("GET", f"/payment_links/{_safe_id(link_id)}", settings=settings)


def cancel_payment_link(link_id: str, field_order: str | None = None, settings=None) -> dict:
	return _logged(
		"Cancel Payment Link",
		field_order,
		None,
		lambda: request("POST", f"/payment_links/{_safe_id(link_id)}/cancel", settings=settings),
	)


def _link_payload(doc, reference_id: str, settings, notify: bool = True) -> dict:
	retailer = (
		frappe.db.get_value("Retailer", doc.retailer, ["retailer_name", "phone", "email"], as_dict=True) or {}
	)
	contact = indian_phone(retailer.get("phone"))
	email = (retailer.get("email") or "").strip()
	customer = {
		"name": doc.retailer_name or retailer.get("retailer_name"),
		"contact": contact,
		"email": email,
	}

	payload = {
		"amount": int(round(flt(doc.grand_total) * 100)),  # paise  # noqa: RUF046
		"currency": frappe.db.get_default("currency") or "INR",
		"accept_partial": False,
		"description": f"Field Order {doc.name}",
		"reference_id": reference_id,
		"customer": {k: v for k, v in customer.items() if v},
		"notify": {
			"sms": bool(notify and settings.notify_sms and contact),
			"email": bool(notify and settings.notify_email and email),
		},
		"reminder_enable": True,
		"notes": {"field_order": doc.name, "sales_rep": doc.sales_rep or "", "retailer": doc.retailer or ""},
	}
	hours = cint(settings.link_expiry_hours)
	if hours > 0:
		payload["expire_by"] = int(time.time()) + max(hours * 3600, MIN_EXPIRY_SECONDS)
	callback_url = (settings.callback_url or "").strip()
	if callback_url:
		payload["callback_url"] = callback_url
		payload["callback_method"] = "get"
	return payload


def indian_phone(phone: str | None) -> str | None:
	"""``+91XXXXXXXXXX`` for a 10-digit Indian mobile (with or without +91 / 0), else None."""
	digits = re.sub(r"\D", "", phone or "")
	if len(digits) == 12 and digits.startswith("91"):
		digits = digits[2:]
	elif len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	return f"+91{digits}" if len(digits) == 10 and digits[0] in "6789" else None


def _safe_id(link_id: str) -> str:
	# Ids go into the URL path; Razorpay ids are plink_ + alphanumerics.
	if not re.fullmatch(r"[A-Za-z0-9_]+", link_id or ""):
		raise RazorpayError(_("Invalid payment link id"), code="INVALID_ID")
	return link_id


def _logged(operation: str, field_order: str | None, payload: dict | None, call):
	"""Run one Razorpay call and write a Sync Log (a failed one survives the rollback)."""
	start = time.monotonic()
	fields = {
		"platform": PLATFORM,
		"operation": operation,
		"reference_doctype": "Field Order" if field_order else None,
		"reference_name": field_order,
	}
	try:
		result = call()
	except RazorpayError as e:
		log_sync_durable(
			**fields,
			status="Failed",
			duration_ms=_elapsed_ms(start),
			http_status=e.http_status,
			error_code=e.code or "RazorpayError",
			error_message=e.message,
			request_payload=frappe.as_json(payload) if payload else None,
		)
		raise
	log_sync(**fields, status="Success", duration_ms=_elapsed_ms(start), external_id=result.get("id"))
	return result


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)


def _from_unix(ts) -> datetime | None:
	"""Unix timestamp -> naive datetime in the system time zone (how Frappe stores Datetime)."""
	if not cint(ts):
		return None
	return datetime.fromtimestamp(cint(ts), tz=ZoneInfo(get_system_timezone())).replace(tzinfo=None)


# ----------------------------------------------------------------------
# Whitelisted
# ----------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def create_link_for_order(field_order: str) -> dict:
	"""
	Create (or return the still-valid) Razorpay payment link for a submitted Field Order.

	The order row is locked for the whole call, so a double click cannot create two links.
	An older link that can still be paid is cancelled first.

	:return: ``{"url", "id", "expires_on"}``
	"""
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	doc.check_permission("write")

	settings = get_settings()
	if not settings.enabled:
		frappe.throw(_("Razorpay payment links are not enabled"))
	if doc.docstatus != 1:
		frappe.throw(_("Submit the order before creating a payment link"))
	if flt(doc.grand_total) <= 0:
		frappe.throw(_("The order total must be more than zero"))
	if doc.payment_status == "Paid":
		frappe.throw(_("This order is already paid"))

	if doc.payment_status == "Link Sent" and doc.razorpay_payment_link_url and not _expired(doc):
		return _link_result(doc)

	try:
		return _replace_link(doc, settings)
	except RazorpayError as e:
		frappe.throw(_("Razorpay: {0}").format(e.message), title=_("Payment link not created"))


def link_for_confirmation(doc) -> dict | None:
	"""
	Payment link for the order confirmation email: the stored one if it can still be paid, else a new one.

	Razorpay is told not to notify the customer, so the email is the only place the link is sent.
	Never raises: without a link the email still goes out (the failure is in Sync Log / Error Log).

	:return: ``{"url", "id", "expires_on"}`` or None
	"""
	settings = get_settings()
	if not settings.enabled or flt(doc.grand_total) <= 0 or doc.payment_status in ("Paid", "Partially Paid"):
		return None
	if doc.payment_status == "Link Sent" and doc.razorpay_payment_link_url and not _expired(doc):
		return _link_result(doc)
	try:
		return _replace_link(doc, settings, notify=False)
	except Exception:
		frappe.log_error(title=f"Payment link for confirmation failed: {doc.name}")
		return None


def _replace_link(doc, settings, notify: bool = True) -> dict:
	"""
	Retire the order's old link, then create and store a new one.

	:raises RazorpayError: if the old link was already cancelled, the order is committed as Link Cancelled first.
	"""
	cancelled = False
	try:
		previous_reference = None
		if doc.razorpay_payment_link_id:
			previous_reference, cancelled = _retire_link(doc, settings)
		link = _create_unique(doc, settings, previous_reference, notify=notify)
	except RazorpayError:
		if cancelled:
			# The old link is gone on Razorpay: record that even though creating failed.
			frappe.db.rollback()
			doc.db_set("payment_status", "Link Cancelled", update_modified=False)
			frappe.db.commit()
		raise

	doc.db_set(
		{
			"razorpay_payment_link_id": link.get("id"),
			"razorpay_payment_link_url": link.get("short_url"),
			"payment_link_expires_on": _from_unix(link.get("expire_by")),
			"payment_status": "Link Sent",
		},
		update_modified=False,
	)
	doc.add_comment(
		"Info",
		_("Razorpay payment link created: {0}").format(
			escape_html(link.get("short_url") or link.get("id") or "")
		),
	)
	return _link_result(doc)


@frappe.whitelist(methods=["GET"])
def get_payment_config() -> dict:
	"""Whether payment links can be created; never returns the keys."""
	return {"enabled": bool(cint(frappe.db.get_single_value("Razorpay Settings", "enabled")))}


def _expired(doc) -> bool:
	return bool(doc.payment_link_expires_on) and get_datetime(doc.payment_link_expires_on) <= now_datetime()


def _link_result(doc) -> dict:
	return {
		"url": doc.razorpay_payment_link_url,
		"id": doc.razorpay_payment_link_id,
		"expires_on": doc.payment_link_expires_on,
	}


def _retire_link(doc, settings) -> tuple[str | None, bool]:
	"""
	Cancel the order's previous link if it can still be paid.

	:return: ``(its reference_id, whether it was cancelled now)``
	:raises frappe.ValidationError: the previous link was already paid.
	"""
	old = fetch_payment_link(doc.razorpay_payment_link_id, settings=settings)
	status = old.get("status")
	if status == "paid":
		frappe.throw(
			_(
				"The previous payment link {0} is already paid; the order will show as paid once Razorpay confirms it."
			).format(doc.razorpay_payment_link_id)
		)
	cancelled = False
	if status in OPEN_LINK_STATES:
		cancel_payment_link(doc.razorpay_payment_link_id, field_order=doc.name, settings=settings)
		cancelled = True
	return old.get("reference_id"), cancelled


def _create_unique(doc, settings, previous_reference: str | None, notify: bool = True) -> dict:
	"""Create the link with the next free reference id: NAME, then NAME-2, NAME-3, ..."""
	n = _reference_number(doc.name, previous_reference) + 1 if previous_reference else 1
	for _try in range(MAX_REFERENCE_TRIES):
		reference_id = doc.name if n == 1 else f"{doc.name}-{n}"
		try:
			return create_payment_link(doc, reference_id=reference_id, settings=settings, notify=notify)
		except RazorpayError as e:
			# Taken by a link this order no longer stores (e.g. created but not saved here).
			if e.http_status == 400 and "reference" in (e.message or "").lower():
				n += 1
				continue
			raise
	raise RazorpayError(_("Could not find a free reference id for this order"), code="REFERENCE_ID")


def _reference_number(name: str, reference_id: str) -> int:
	"""``NAME`` -> 1, ``NAME-3`` -> 3; anything else -> 1."""
	suffix = reference_id[len(name) + 1 :] if reference_id.startswith(f"{name}-") else ""
	return cint(suffix) if suffix.isdigit() else 1
