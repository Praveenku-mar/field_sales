# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify App Store install: the OAuth authorization code grant with an expiring offline token.

1. Shopify (or a merchant's install link) opens ``install`` with ``shop``, ``hmac``, ``timestamp``.
2. ``install`` checks the HMAC and shop, stores a one-time ``state`` in Redis (10 minutes)
   and redirects to the shop's ``/admin/oauth/authorize``.
3. The merchant approves; Shopify redirects to ``callback`` with ``code``, ``state``, ``hmac``.
4. ``callback`` checks everything again, swaps the code for tokens (``expiring=1``), saves
   them on Store Connection (OAuth mode, enabled), queues webhook registration and
   redirects to the app.

Neither endpoint needs a Frappe login (``allow_guest``) or a browser cookie: state lives in
Redis, so a router in front of several sites can forward these requests unchanged.
One site serves one shop: a different shop is refused while the current one is connected.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import now_datetime
from werkzeug.utils import redirect
from werkzeug.wrappers import Response

from field_sales.connectors.shopify import REQUEST_TIMEOUT, save_oauth_tokens
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync_durable
from field_sales.shopify import config

STATE_TTL_SECONDS = 10 * 60
STATE_KEY = "field_sales:shopify_oauth_state:{}"
# Rejected requests are public; log each kind at most this often so a flood can't fill Sync Log.
FAIL_LOG_INTERVAL_SECONDS = 60


class InstallError(Exception):
	"""A check failed; the message is safe to show (no secrets)."""


@frappe.whitelist(allow_guest=True, methods=["GET"])
def install(shop: str | None = None, hmac: str | None = None, timestamp: str | None = None, **kwargs):
	"""
	Start the install: verify the request and redirect (302) to Shopify's authorize page.

	The HMAC covers every query parameter, so it is checked against the raw query string,
	not only the named arguments. Any failure answers 400 with a plain message.
	"""
	try:
		shop = _check_signed_request()
		check_shop_allowed(shop)

		state = secrets.token_urlsafe(32)
		frappe.cache.set(_state_key(state), shop, ex=STATE_TTL_SECONDS)
		query = urlencode(
			{
				"client_id": config.client_id(),
				"scope": config.scopes(),
				"redirect_uri": config.public_url(config.CALLBACK_PATH),
				"state": state,
			}
		)
	except InstallError as e:
		return _fail(str(e), shop)

	return redirect(f"https://{shop}/admin/oauth/authorize?{query}", code=302)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def callback(
	code: str | None = None,
	shop: str | None = None,
	state: str | None = None,
	hmac: str | None = None,
	**kwargs,
):
	"""
	Finish the install: verify, exchange the code for tokens, save them, redirect to the app.

	A reinstall runs the same way and overwrites the old tokens.
	"""
	start = time.monotonic()
	try:
		shop = _check_signed_request()
		expected_shop = _consume_state(state)
		if expected_shop != shop:
			raise InstallError("Unknown or expired install request; start the install again.")
		if not code:
			raise InstallError("Missing authorization code.")
		check_shop_allowed(shop)
		body = _exchange_code(shop, code)
		missing = save_installation(shop, body)
	except InstallError as e:
		return _fail(str(e), shop)

	log_sync_durable(
		platform="Shopify",
		operation="Install",
		status="Success",
		endpoint=f"https://{shop}/admin/oauth/access_token",
		duration_ms=int((time.monotonic() - start) * 1000),
		external_id=shop,
		error_message=f"Missing scopes: {', '.join(missing)}" if missing else None,
	)
	frappe.db.commit()
	return redirect(config.APP_HOME_PATH, code=302)


# ----------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------


def _check_signed_request() -> str:
	"""Verify the query HMAC and the shop domain; return the shop."""
	if not config.client_id() or not config.client_secret():
		raise InstallError("The Shopify app is not configured on this site.")

	args = frappe.request.args
	if not config.verify_query_hmac(args, config.client_secret()):
		raise InstallError("Invalid signature.")

	shop = (args.get("shop") or "").strip().lower()
	if not config.is_valid_shop(shop):
		raise InstallError("Invalid shop domain.")
	return shop


def check_shop_allowed(shop: str) -> None:
	"""One site, one shop: refuse a different shop while one is connected (enabled)."""
	settings = frappe.get_single("Store Connection")
	connected = settings.enabled and settings.platform == "Shopify" and settings.store_url
	if connected and settings.store_url.lower() != shop:
		raise InstallError("This site is already connected to another shop.")


def _consume_state(state: str | None) -> str | None:
	"""Return the shop stored for ``state`` and delete it, so it can be used only once."""
	if not state:
		return None
	# GETDEL reads and removes in one step, so two requests can't both use it.
	shop = frappe.cache.getdel(_state_key(state))
	return shop.decode() if isinstance(shop, bytes) else shop


def _state_key(state: str) -> str:
	return frappe.cache.make_key(STATE_KEY.format(state))


# ----------------------------------------------------------------------
# Token exchange and save
# ----------------------------------------------------------------------


def _exchange_code(shop: str, code: str) -> dict:
	"""POST the code to Shopify for an expiring offline token; return the JSON body."""
	url = f"https://{shop}/admin/oauth/access_token"
	try:
		response = requests.post(
			url,
			data={
				"client_id": config.client_id(),
				"client_secret": config.client_secret(),
				"code": code,
				"expiring": "1",
			},
			headers={"Accept": "application/json"},
			timeout=REQUEST_TIMEOUT,
		)
	except requests.RequestException as e:
		raise InstallError(f"Could not reach Shopify ({type(e).__name__}).") from e

	try:
		body = response.json()
	except ValueError:
		body = None
	if not response.ok or not isinstance(body, dict) or not body.get("access_token"):
		raise InstallError(f"Shopify did not issue a token (HTTP {response.status_code}).")
	return body


def save_installation(shop: str, body: dict) -> list[str]:
	"""
	Point Store Connection at ``shop`` in OAuth mode with the new tokens, then queue
	webhook registration. Saved as Administrator (the request is a guest's).

	:return: Requested scopes the merchant did not grant.
	"""
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		settings = frappe.get_single("Store Connection")
		settings.update(
			{
				"platform": "Shopify",
				"shopify_auth_mode": config.OAUTH_MODE,
				"store_url": shop,
				"enabled": 1,
				"installed_on": now_datetime(),
				"uninstalled_on": None,
				"last_test_status": "Installed from Shopify",
				"last_tested_on": now_datetime(),
			}
		)
		settings.flags.from_store_install = True
		settings.save(ignore_permissions=True)
		# After the save: validate() may clear tokens of the previous shop or mode.
		save_oauth_tokens(body)
	finally:
		frappe.set_user(previous_user)

	frappe.enqueue(
		"field_sales.shopify.webhooks.register_webhooks",
		queue="short",
		enqueue_after_commit=True,
		job_id="field_sales_shopify_register_webhooks",
		deduplicate=True,
	)
	return config.missing_scopes(body.get("scope"))


def _fail(message: str, shop: str | None) -> Response:
	"""400 with a plain message, and a failed Install log (no query string: it holds the HMAC)."""
	frappe.db.rollback()
	throttle = frappe.cache.make_key(f"field_sales:shopify_install_failed:{message}")
	if not frappe.cache.set(throttle, 1, nx=True, ex=FAIL_LOG_INTERVAL_SECONDS):
		return Response(message, status=400, mimetype="text/plain")
	log_sync_durable(
		platform="Shopify",
		operation="Install",
		status="Failed",
		http_status=400,
		external_id=(shop or "")[:140] or None,
		error_code="INSTALL_REJECTED",
		error_message=message,
	)
	frappe.db.commit()
	return Response(message, status=400, mimetype="text/plain")
