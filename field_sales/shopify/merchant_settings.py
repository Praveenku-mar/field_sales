# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Settings the merchant manages from inside the Shopify admin (embedded app).

Store Connection and Email Account are System Manager doctypes; the embedded user is a Sales
Manager. These endpoints let a manager change only the merchant-facing settings — locations,
default location, order mode, product sync schedule, invoice details, the commission switch and
the outgoing email account — and only on a request signed by the connected shop's session token
(``session_token.authenticate`` sets ``frappe.local.shopify_shop``), or as a System Manager.
Credentials, the store URL and the auth mode are never exposed or changed here.

``setup_new_store`` (queued by ``oauth.save_installation``) picks the first store location as the
default on a fresh install, so products start syncing without anyone opening Settings.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from field_sales.utils.guards import is_system_manager
from field_sales.utils.db import safe_commit

MANAGER_ROLES = {"System Manager", "Sales Manager"}

# Store Connection fields the merchant may change (the Settings forms send the whole doc).
EDITABLE_FIELDS = (
	"order_mode",
	"product_sync_frequency",
	"default_location_id",
	"default_location_name",
	"send_order_confirmation",
	"company_name",
	"company_address",
	"company_state",
	"company_gstin",
	"company_phone",
	"company_email",
	"enable_commission",
)
# Shown read-only.
READ_FIELDS = (
	"name",
	"modified",
	"platform",
	"enabled",
	"store_url",
	"shop_name",
	"shopify_scopes",
	"installed_on",
	"last_test_status",
	"last_tested_on",
	"shop_currency",
	"shop_timezone",
	"shop_country",
)
LOCATION_FIELDS = ("location_id", "location_name", "sell_from")

EMAIL_FIELDS = (
	"service",
	"smtp_server",
	"smtp_port",
	"use_tls",
	"use_ssl_for_outgoing",
	"login_id_is_different",
	"login_id",
	"enable_outgoing",
	"default_outgoing",
	"always_use_account_email_id_as_sender",
)
EMAIL_LIST_FIELDS = ("name", "email_id", *EMAIL_FIELDS)
# Where and how the account signs in. Frappe checks a saved account by signing in with its stored
# password, so changing any of these without giving the password would send that password to the
# new server: the password must come with the change.
EMAIL_CONNECTION_FIELDS = (
	"service",
	"smtp_server",
	"smtp_port",
	"use_tls",
	"use_ssl_for_outgoing",
	"login_id_is_different",
	"login_id",
)
EMAIL_CHECK_FIELDS = ("use_tls", "use_ssl_for_outgoing", "login_id_is_different")
# Site default (JSON list) of the Email Accounts a merchant added from the Email tab: the only ones
# a merchant may see and change. The site's other accounts are for System Managers.
MERCHANT_EMAIL_ACCOUNTS_KEY = "field_sales_merchant_email_accounts"


def _require_merchant() -> None:
	"""A System Manager, or a manager on a request signed by the connected shop's session token."""
	if is_system_manager():
		return
	shop = getattr(frappe.local, "shopify_shop", None)
	store = (frappe.db.get_single_value("Store Connection", "store_url") or "").lower()
	if shop and shop == store and MANAGER_ROLES & set(frappe.get_roles()):
		return
	frappe.throw(_("Only the store's managers can change these settings"), frappe.PermissionError)


# ----------------------------------------------------------------------
# Store Connection
# ----------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_store_settings() -> dict:
	"""The merchant-facing part of Store Connection, shaped like a document for the Settings forms."""
	frappe.only_for(list(MANAGER_ROLES))
	_require_merchant()
	settings = frappe.get_single("Store Connection")
	values = {f: settings.get(f) for f in (*READ_FIELDS, *EDITABLE_FIELDS)}
	values["doctype"] = "Store Connection"
	values["locations"] = [
		{"doctype": "Store Location", **{f: row.get(f) for f in LOCATION_FIELDS}}
		for row in settings.locations
	]
	return values


@frappe.whitelist(methods=["POST"])
def save_store_settings(values: dict | str) -> dict:
	"""
	Save the editable fields (others in ``values`` are ignored) and the locations table.
	Queues a product sync when the default location changes.
	"""
	frappe.only_for(list(MANAGER_ROLES))
	_require_merchant()
	values = frappe.parse_json(values) or {}
	settings = frappe.get_single("Store Connection")
	before_default = settings.default_location_id

	for field in EDITABLE_FIELDS:
		if field in values:
			settings.set(field, values[field])
	if "locations" in values:
		settings.set("locations", [])
		for row in values.get("locations") or []:
			location_id = str(row.get("location_id") or "").strip()
			if location_id:
				settings.append(
					"locations",
					{
						"location_id": location_id,
						"location_name": row.get("location_name"),
						"sell_from": cint(row.get("sell_from")),
					},
				)
	settings.save(ignore_permissions=True)

	if settings.default_location_id and settings.default_location_id != before_default:
		_queue_product_sync()
	return get_store_settings()


def _queue_product_sync() -> None:
	"""Like ``sync.products.enqueue_sync()``, but only once this request's save is committed."""
	from field_sales.sync.products import JOB_ID

	frappe.enqueue(
		"field_sales.sync.products.sync_products",
		queue="long",
		timeout=1500,
		job_id=JOB_ID,
		deduplicate=True,
		enqueue_after_commit=True,
	)


@frappe.whitelist(methods=["GET"])
def fetch_locations() -> list[dict]:
	"""The store's locations ``[{"id", "name"}]`` (for Fetch Locations)."""
	frappe.only_for(list(MANAGER_ROLES))
	_require_merchant()
	from field_sales.connectors import get_connector

	return get_connector().get_locations()


def setup_new_store() -> None:
	"""
	Background job after an install: if no default location is set yet, use the store's first
	location (ticked Sell from here) and start the first product sync. Never overrides a choice.
	"""
	settings = frappe.get_single("Store Connection")
	if settings.default_location_id or not settings.enabled:
		return
	from field_sales.connectors import get_connector
	from field_sales.sync.products import enqueue_sync

	locations = get_connector().get_locations()
	if not locations:
		return
	first = locations[0]
	settings.default_location_id = str(first["id"])
	settings.default_location_name = first.get("name")
	if not any(r.location_id == settings.default_location_id for r in settings.locations):
		settings.append(
			"locations",
			{"location_id": settings.default_location_id, "location_name": first.get("name"), "sell_from": 1},
		)
	settings.save(ignore_permissions=True)
	safe_commit()
	enqueue_sync()


# ----------------------------------------------------------------------
# Outgoing email account
# ----------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_email_accounts() -> list[dict]:
	"""
	Email accounts (no passwords) for the Email settings tab: every account for a System
	Manager, only those added from the tab (``_merchant_accounts``) for a merchant.
	"""
	frappe.only_for(list(MANAGER_ROLES))
	_require_merchant()
	filters = None if is_system_manager() else {"name": ("in", list(_merchant_accounts()) or [""])}
	return _email_accounts(filters)


def _email_accounts(filters: dict | None) -> list[dict]:
	"""Email Account rows for the tab. A merchant (Sales Manager) cannot read Email Account, so
	the rows are read here; the caller limits them to the merchant's own accounts."""
	return frappe.get_all(
		"Email Account",
		filters=filters,
		fields=list(EMAIL_LIST_FIELDS),
		order_by="default_outgoing desc, modified desc",
		limit=50,
	)


@frappe.whitelist(methods=["POST"])
def save_email_account(values: dict | str, name: str | None = None) -> dict:
	"""
	Create (``name`` empty) or update an outgoing email account with the allowed fields only.
	Frappe checks the SMTP login on save, so a wrong password is refused with its message.

	A merchant may change only an account added from this tab. Changing where or how an account
	signs in needs its password in the same save: Frappe would otherwise sign in to the new server
	with the stored password.
	"""
	frappe.only_for(list(MANAGER_ROLES))
	_require_merchant()
	values = frappe.parse_json(values) or {}
	clean = {f: values[f] for f in EMAIL_FIELDS if f in values}
	if values.get("password"):
		clean["password"] = values["password"]

	if name:
		if not is_system_manager() and name not in _merchant_accounts():
			frappe.throw(
				_("This email account was not added here. Ask a System Manager to change it."),
				frappe.PermissionError,
			)
		doc = frappe.get_doc("Email Account", name)
		if not clean.get("password") and _connection_changed(doc, clean):
			frappe.throw(_("Enter the password again to change the server or sign-in settings."))
		doc.update(clean)
	else:
		email_id = (values.get("email_id") or "").strip()
		if not email_id:
			frappe.throw(_("Email address is required"))
		doc = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_id": email_id,
				"email_account_name": (values.get("email_account_name") or "").strip() or None,
				"auth_method": "Basic",
				"enable_incoming": 0,
				**clean,
				"enable_outgoing": 1,
			}
		)
	if name:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)
		_remember_merchant_account(doc.name)
	return {"name": doc.name}


def _merchant_accounts() -> set[str]:
	"""Names of the Email Accounts added from the Email tab."""
	names = frappe.parse_json(frappe.db.get_default(MERCHANT_EMAIL_ACCOUNTS_KEY) or "[]")
	return {n for n in names if isinstance(n, str)} if isinstance(names, list) else set()


def _remember_merchant_account(name: str) -> None:
	names = _merchant_accounts() | {name}
	frappe.db.set_default(MERCHANT_EMAIL_ACCOUNTS_KEY, frappe.as_json(sorted(names), indent=None))


def _connection_changed(doc, values: dict) -> bool:
	"""Whether ``values`` change where or how the saved account ``doc`` signs in."""
	for field in EMAIL_CONNECTION_FIELDS:
		if field not in values:
			continue
		new, old = values[field], doc.get(field)
		if field in EMAIL_CHECK_FIELDS:
			if cint(new) != cint(old):
				return True
		elif str(new or "").strip().lower() != str(old or "").strip().lower():
			return True
	return False
