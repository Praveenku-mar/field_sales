# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Field Sales embedded in the Shopify admin (App Bridge, Shopify managed installation).

- ``page_context``: hook for ``www/fieldsales.py`` (``field_sales_embedded_page`` in hooks.py).
  Decides whether the page request comes from the Shopify admin, runs the token exchange on
  the first load after a (re)install, and returns the boot values for embedded mode.
- ``after_request``: lets the admin (and only the shop's admin) frame that page.
- ``ensure_offline_token``: token exchange (ID token -> expiring offline token), saved through
  ``oauth.save_installation`` like the authorization code grant.
- ``get_embedded_user``: the Frappe user embedded requests run as.
- ``get_store_status``: read-only connection summary for the embedded Settings page.

Shopify managed installation installs the app and grants the scopes from
``shopify.app.toml``; the app gets its first token by exchanging an ID token.
"""

from __future__ import annotations

import time

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, now_datetime

from field_sales.connectors.shopify import REQUEST_TIMEOUT, TOKEN_LOCK_KEY, TOKEN_LOCK_SECONDS
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync_durable
from field_sales.shopify import config
from field_sales.shopify.oauth import InstallError, check_shop_allowed, save_installation
from field_sales.shopify.session_token import InvalidSessionToken, decode

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
OFFLINE_TOKEN_TYPE = "urn:shopify:params:oauth:token-type:offline-access-token"

EMBEDDED_ROLE = "Sales Manager"
MANAGER_ROLES = {"System Manager", "Sales Manager"}


class ConnectError(Exception):
	"""The shop can't be connected to this site; a new ID token would not help."""


# ----------------------------------------------------------------------
# Connection and token exchange
# ----------------------------------------------------------------------


def is_connected(shop: str) -> bool:
	"""Store Connection is this shop's, in OAuth mode, with tokens that still work without the merchant."""
	settings = frappe.get_single("Store Connection")
	if (settings.store_url or "").lower() != shop or settings.shopify_auth_mode != config.OAUTH_MODE:
		return False
	if not settings.enabled:
		return False
	if settings.refresh_token_expiry:
		return get_datetime(settings.refresh_token_expiry) > now_datetime()
	# A non-expiring offline token has no refresh token.
	return bool(settings.get_password("access_token", raise_exception=False)) and not settings.token_expiry


def ensure_offline_token(shop: str, id_token: str) -> None:
	"""
	Exchange ``id_token`` for an expiring offline token and save it as the shop's installation
	(fresh install, reinstall, or after REAUTH_REQUIRED). Commits.

	Holds the connector's token lock: an exchange and a refresh at the same time would retire
	each other's tokens. Another request that finished the exchange first wins.

	:raises InvalidSessionToken: Shopify refused the ID token (expired): the client retries.
	:raises ConnectError: The shop may not be connected here, or Shopify failed otherwise.
	"""
	lock = frappe.cache.lock(
		frappe.cache.make_key(TOKEN_LOCK_KEY), timeout=TOKEN_LOCK_SECONDS, blocking_timeout=TOKEN_LOCK_SECONDS
	)
	if not lock.acquire():
		raise ConnectError(_("Timed out waiting for another Shopify token request"))
	try:
		# A fresh snapshot: another request may have just saved the tokens.
		frappe.db.commit()
		if is_connected(shop):
			return
		try:
			check_shop_allowed(shop)
		except InstallError as e:
			_log_install("Failed", shop, str(e))
			raise ConnectError(str(e)) from e

		start = time.monotonic()
		body = _exchange(shop, id_token)
		missing = save_installation(shop, body)
		_log_install(
			"Success",
			shop,
			f"Token exchange. Missing scopes: {', '.join(missing)}" if missing else "Token exchange",
			duration_ms=int((time.monotonic() - start) * 1000),
		)
		frappe.db.commit()
	finally:
		try:
			lock.release()
		except Exception:
			pass  # timed out and taken over


def _exchange(shop: str, id_token: str) -> dict:
	url = f"https://{shop}/admin/oauth/access_token"
	try:
		response = requests.post(
			url,
			data={
				"client_id": config.client_id(),
				"client_secret": config.client_secret(),
				"grant_type": TOKEN_EXCHANGE_GRANT,
				"subject_token": id_token,
				"subject_token_type": ID_TOKEN_TYPE,
				"requested_token_type": OFFLINE_TOKEN_TYPE,
				"expiring": "1",
			},
			headers={"Accept": "application/json"},
			timeout=REQUEST_TIMEOUT,
		)
	except requests.RequestException as e:
		_log_install("Failed", shop, f"Could not reach Shopify ({type(e).__name__})")
		raise ConnectError(_("Could not reach Shopify")) from e

	try:
		body = response.json()
	except ValueError:
		body = None
	if response.status_code == 400:
		# Expired or otherwise invalid ID token: App Bridge retries with a new one.
		raise InvalidSessionToken("Shopify refused the ID token")
	if not response.ok or not isinstance(body, dict) or not body.get("access_token"):
		_log_install("Failed", shop, f"Token exchange failed (HTTP {response.status_code})")
		raise ConnectError(_("Shopify did not issue a token (HTTP {0})").format(response.status_code))
	return body


def _log_install(status: str, shop: str, message: str, **fields) -> None:
	log_sync_durable(
		platform="Shopify",
		operation="Install",
		status=status,
		endpoint=f"https://{shop}/admin/oauth/access_token",
		external_id=shop,
		error_code=None if status == "Success" else "TOKEN_EXCHANGE",
		error_message=message,
		**fields,
	)


# ----------------------------------------------------------------------
# Embedded user
# ----------------------------------------------------------------------


def get_embedded_user() -> str:
	"""
	The user requests from the Shopify admin run as: Store Connection's ``embedded_user``,
	else ``shopify-admin@<site>`` (created on first use with the Sales Manager role and its own
	Sales Rep, so the manager screens and Reports To work for it).
	"""
	user = frappe.db.get_single_value("Store Connection", "embedded_user")
	if user and frappe.db.get_value("User", user, "enabled"):
		return user

	email = f"shopify-admin@{frappe.local.site}"
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Shopify",
					"last_name": "Admin",
					"user_type": "System User",
					"send_welcome_email": 0,
					"roles": [{"role": EMBEDDED_ROLE}],
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("Sales Rep", {"user": email}):
			frappe.get_doc(
				{"doctype": "Sales Rep", "user": email, "full_name": "Shopify Admin", "active": 1}
			).insert(ignore_permissions=True)
		frappe.db.set_single_value("Store Connection", "embedded_user", email)
		frappe.db.commit()
	finally:
		frappe.set_user(previous_user)
	return email


# ----------------------------------------------------------------------
# Page (www/fieldsales.py) and headers
# ----------------------------------------------------------------------


def page_context() -> dict | None:
	"""
	Boot values for an embedded page load, or ``None`` for the normal (cookie) app.

	Embedded = the query has ``shop`` and ``host`` (and ``embedded=1`` or ``id_token``) and the
	shop is this site's, in OAuth mode. On the first load after a (re)install the shop is not
	saved yet: then the ``id_token`` in the query must verify, and is exchanged for tokens.
	"""
	request = getattr(frappe, "request", None)
	if not request:
		return None

	# A WooCommerce store frames the same page: allow its origin (cookie app below).
	settings = frappe.get_single("Store Connection")
	if settings.platform == "WooCommerce" and settings.enabled and settings.store_url:
		frappe.local.woo_frame_store = (settings.store_url or "").rstrip("/")

	args = request.args
	shop = (args.get("shop") or "").strip().lower()
	host = (args.get("host") or "").strip()
	id_token = args.get("id_token") or ""
	if not (shop and host and (args.get("embedded") == "1" or id_token)):
		return None
	if not config.is_valid_shop(shop) or not config.client_id():
		return None

	settings = frappe.get_single("Store Connection")
	store = (settings.store_url or "").lower()
	if store == shop and settings.shopify_auth_mode == config.OAUTH_MODE:
		if id_token and not is_connected(shop):
			_try_exchange(shop, id_token)
	else:
		# Not this site's shop (yet): only a valid ID token for an allowed shop gets in.
		try:
			claims = decode(id_token, require_connected=False)
		except InvalidSessionToken:
			return None
		if claims["shop"] != shop:
			return None
		_try_exchange(shop, id_token)

	if args.get("plan_handle"):
		# Back from Shopify's plan page (welcome link): the plan may have changed.
		from field_sales.shopify import billing

		billing.clear_cache()

	frappe.local.shopify_frame_shop = shop
	return {
		"embedded": True,
		"shop": shop,
		"host": host,
		"shopify_api_key": config.client_id(),
	}


def _try_exchange(shop: str, id_token: str) -> None:
	"""Exchange on page load if possible; otherwise the app's first API call retries it."""
	try:
		decode(id_token, require_connected=False)
		ensure_offline_token(shop, id_token)
	except InvalidSessionToken, ConnectError:
		pass


def after_request(response=None, request=None):
	"""
	``after_request`` hook: an embedded page may be framed by its shop's admin only
	(``frame-ancestors https://{shop} https://admin.shopify.com``), with no X-Frame-Options.
	Other responses are left alone.
	"""
	shop = getattr(frappe.local, "shopify_frame_shop", None)
	if shop and response is not None:
		response.headers["Content-Security-Policy"] = f"frame-ancestors https://{shop} https://admin.shopify.com;"
		response.headers.pop("X-Frame-Options", None)

	store = getattr(frappe.local, "woo_frame_store", None)
	if store and response is not None:
		response.headers["Content-Security-Policy"] = f"frame-ancestors {store};"
		response.headers.pop("X-Frame-Options", None)


# ----------------------------------------------------------------------
# Embedded Settings
# ----------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_store_status() -> dict:
	"""Connected shop, granted scopes, locations and order mode, for managers (read-only)."""
	if not (MANAGER_ROLES & set(frappe.get_roles())) and frappe.session.user != "Administrator":
		frappe.throw(_("Only managers can see the store connection"), frappe.PermissionError)
	settings = frappe.get_single("Store Connection")
	return {
		"shop": settings.store_url,
		"shop_name": settings.shop_name,
		"enabled": bool(settings.enabled),
		"status": settings.last_test_status,
		"scopes": [s.strip() for s in (settings.shopify_scopes or "").split(",") if s.strip()],
		"installed_on": settings.installed_on,
		"order_mode": settings.order_mode,
		"default_location_id": settings.default_location_id,
		"locations": [
			{"location_id": r.location_id, "location_name": r.location_name, "sell_from": bool(r.sell_from)}
			for r in settings.locations
		],
	}
