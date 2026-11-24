# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify app lifecycle and privacy compliance webhooks.

One public endpoint, ``receive``, for ``app/uninstalled`` (registered by ``register_webhooks``
after install) and the three mandatory compliance topics (declared in ``shopify.app.toml``;
they can't be registered through the API). Store data topics (stock, products, orders) keep
their own endpoint, ``field_sales.api.webhooks.receive``.

``receive`` checks the HMAC, the shop and the delivery id, records the request and queues
the work, so Shopify gets its 200 within its 5 second limit.
"""

from __future__ import annotations

import json
from datetime import UTC

import frappe
from frappe.utils import get_datetime, now_datetime

from field_sales.connectors.shopify import clear_oauth_tokens
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync, log_sync_or_error
from field_sales.shopify import config
from field_sales.utils.store import (
	enqueue_in_store,
	get_store,
	set_current_store,
	set_store_value,
	store_for_shop,
)

DEDUP_TTL_SECONDS = 24 * 60 * 60
REJECTED_LOG_INTERVAL_SECONDS = 60

UNINSTALLED_TOPIC = "app/uninstalled"
COMPLIANCE_TOPICS = ("customers/data_request", "customers/redact", "shop/redact")

# Topics registered through the Admin API (GraphQL enum name -> Shopify topic).
API_TOPICS = {"APP_UNINSTALLED": UNINSTALLED_TOPIC}
# Store data the sync needs, registered through the API too (to ``config.STORE_WEBHOOK_PATH``), so
# they reach this site's own address whatever URL the deployed shopify.app.toml holds. When the
# toml declares them as well, an event arrives twice; processing is idempotent.
STORE_API_TOPICS = (
	"INVENTORY_LEVELS_UPDATE",
	"PRODUCTS_UPDATE",
	"PRODUCTS_DELETE",
	"ORDERS_CREATE",
	"ORDERS_PAID",
	"ORDERS_FULFILLED",
	"ORDERS_CANCELLED",
	"REFUNDS_CREATE",
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive():
	"""
	Verify and queue one webhook delivery.

	401 for a bad or missing ``X-Shopify-Hmac-Sha256``; otherwise 200 (also for a shop that is
	not one of this site's Stores, a duplicate delivery or an unhandled topic) so Shopify does
	not retry. The delivery is recorded and queued for the Store of ``X-Shopify-Shop-Domain``.
	"""
	# Read first: the signature covers these exact bytes.
	raw_body = frappe.request.get_data()
	headers = frappe.request.headers
	topic = (headers.get("X-Shopify-Topic") or "").strip()
	shop = (headers.get("X-Shopify-Shop-Domain") or "").strip().lower()
	store = store_for_shop(shop)
	settings = get_store(store) if store else None
	# The rest of the request (logs, the Privacy Request, the job) belongs to the shop's Store.
	set_current_store(store)

	if not config.verify_body_hmac(raw_body, headers.get("X-Shopify-Hmac-Sha256"), _secrets(settings)):
		_log_throttled("INVALID_SIGNATURE", "Webhook signature did not match", 401)
		return _respond(401, "invalid signature")

	if not store:
		_log_throttled("OTHER_SHOP", f"Ignored {topic} for {shop[:100]}: not this site's shop", 200)
		return _respond(200, "ignored")
	if topic != UNINSTALLED_TOPIC and topic not in COMPLIANCE_TOPICS:
		return _respond(200, "ignored")

	webhook_id = (headers.get("X-Shopify-Webhook-Id") or "").strip()
	key = frappe.cache.make_key(f"field_sales:shopify_app_webhook:{webhook_id}") if webhook_id else None
	# NX: only the first delivery of an id sets the key.
	if key and not frappe.cache.set(key, 1, nx=True, ex=DEDUP_TTL_SECONDS):
		return _respond(200, "duplicate")

	try:
		payload = json.loads(raw_body or b"{}")
	except ValueError:
		payload = {}
	if not isinstance(payload, dict):
		payload = {}

	try:
		_queue(topic, shop, payload, webhook_id, headers)
	except Exception:
		# Not queued: forget the id so Shopify's retry is accepted.
		if key:
			frappe.cache.delete(key)
		raise
	return _respond(200, "ok")


def _queue(topic: str, shop: str, payload: dict, webhook_id: str, headers) -> None:
	"""Queue the job for one verified delivery, in the current store."""
	if topic == UNINSTALLED_TOPIC:
		enqueue_in_store(
			"field_sales.shopify.webhooks.handle_uninstalled",
			queue="short",
			shop=shop,
			triggered_at=headers.get("X-Shopify-Triggered-At"),
		)
		return
	# Recorded now (with the current store), so the request is on file even if the job is lost.
	request = _record_privacy_request(topic, shop, payload, webhook_id)
	enqueue_in_store(
		"field_sales.shopify.privacy.process",
		queue="short" if topic != "shop/redact" else "long",
		enqueue_after_commit=True,
		privacy_request=request,
		payload=payload,
	)


def handle_uninstalled(shop: str, triggered_at: str | None = None):
	"""
	Background job for ``app/uninstalled``: drop the shop's tokens and disable its Store, so
	scheduled syncs stop. Ignored if the shop was (re)installed after the uninstall happened.
	"""
	store = store_for_shop(shop)
	if not store:
		return
	settings = get_store(store)
	if triggered_at and settings.installed_on:
		try:
			uninstalled_at = get_datetime(_to_system_time(triggered_at))
		except Exception:
			uninstalled_at = None
		if uninstalled_at and get_datetime(settings.installed_on) > uninstalled_at:
			log_sync(
				platform="Shopify",
				operation="Uninstall",
				status="Success",
				external_id=shop,
				error_message="Ignored: the shop reinstalled the app after this uninstall",
			)
			return

	now = now_datetime()
	clear_oauth_tokens(store)
	set_store_value(
		{
			"enabled": 0,
			"uninstalled_on": now,
			"last_test_status": "Uninstalled from Shopify",
			"last_tested_on": now,
		},
		store=store,
	)
	log_sync(platform="Shopify", operation="Uninstall", status="Success", external_id=shop)


def register_webhooks():
	"""
	Background job after install: subscribe ``app/uninstalled`` to ``receive``, and the store topics
	(``STORE_API_TOPICS``) to the store webhook endpoint, unless they already are. Safe to run again
	(e.g. after the site's public address changes: ``bench execute
	field_sales.shopify.webhooks.register_webhooks``).

	Compliance topics are declared in ``shopify.app.toml`` instead (Shopify does not allow them
	through the API). Failures are in Sync Log (operation Register Webhooks) and not retried.
	"""
	from field_sales.connectors import get_connector

	conn = get_connector()
	uri = config.public_url(config.WEBHOOK_PATH)
	results = {}
	for topic in API_TOPICS:
		results[topic] = conn.ensure_webhook(topic, uri)
	store_uri = config.public_url(config.STORE_WEBHOOK_PATH)
	for topic in STORE_API_TOPICS:
		results[topic] = conn.ensure_webhook(topic, store_uri)
	log_sync(
		platform="Shopify",
		operation="Register Webhooks",
		status="Success",
		endpoint=uri,
		response_payload=frappe.as_json(results),
	)
	return results


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _secrets(settings) -> list[str]:
	"""
	Keys a delivery may be signed with: the App Store app's, and the Store's client credentials
	app's (``settings`` is ``None`` for a shop that is not one of this site's Stores).
	"""
	secrets = [config.client_secret()]
	if settings:
		secrets.append(settings.get_password("shopify_client_secret", raise_exception=False) or "")
	return secrets


def _record_privacy_request(topic: str, shop: str, payload: dict, webhook_id: str) -> str:
	"""Insert the Privacy Request (status Received) with ids only, never the customer's details."""
	customer = payload.get("customer") or {}
	data_request = payload.get("data_request") or {}
	doc = frappe.get_doc(
		{
			"doctype": "Privacy Request",
			"topic": topic,
			"shop_domain": shop,
			"shopify_customer_id": str(customer["id"]) if customer.get("id") else None,
			"request_id": str(data_request["id"]) if data_request.get("id") else None,
			"webhook_id": webhook_id or None,
			"received_on": now_datetime(),
			"status": "Received",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _to_system_time(iso: str):
	"""Shopify's UTC timestamp as a naive datetime in the system time zone."""
	from datetime import datetime

	from frappe.utils import convert_utc_to_system_timezone

	value = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
	if value.tzinfo:
		value = value.astimezone(UTC).replace(tzinfo=None)
	return convert_utc_to_system_timezone(value).replace(tzinfo=None)


def _respond(status: int, message: str) -> dict:
	frappe.local.response.http_status_code = status
	return {"ok": status == 200, "message": message}


def _log_throttled(code: str, message: str, http_status: int) -> None:
	"""Log a rejected or ignored delivery, at most once a minute per kind."""
	key = frappe.cache.make_key(f"field_sales:shopify_app_webhook_logged:{code}")
	if frappe.cache.set(key, 1, nx=True, ex=REJECTED_LOG_INTERVAL_SECONDS):
		log_sync_or_error(
			platform="Shopify",
			operation="Webhook",
			status="Failed",
			http_status=http_status,
			error_code=code,
			error_message=message,
		)
