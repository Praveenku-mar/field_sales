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
from contextlib import suppress

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, now_datetime

from field_sales.connectors.shopify import REQUEST_TIMEOUT, TokenLockTimeout, shop_token_lock
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync_or_error
from field_sales.permissions import MANAGER_ROLES, STORE_ADMIN_ROLE
from field_sales.shopify import config
from field_sales.shopify.oauth import InstallError, check_shop_allowed, save_installation
from field_sales.shopify.session_token import InvalidSessionToken, decode
from field_sales.utils.db import safe_commit
from field_sales.utils.store import (
	assign_user_to_store,
	can_be_embedded_user,
	get_store,
	get_store_name,
	get_store_value,
	is_multi_store,
	set_store_value,
	store_for_shop,
)

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
OFFLINE_TOKEN_TYPE = "urn:shopify:params:oauth:token-type:offline-access-token"

# The embedded user is the merchant's owner: a manager, and Store Admin for the store's settings.
EMBEDDED_ROLES = ("Sales Manager", STORE_ADMIN_ROLE)


class ConnectError(Exception):
	"""The shop can't be connected to this site; a new ID token would not help."""


# ----------------------------------------------------------------------
# Connection and token exchange
# ----------------------------------------------------------------------


def is_connected(shop: str) -> bool:
	"""The shop has its Store, in OAuth mode, with tokens that still work without the merchant."""
	store = store_for_shop(shop)
	if not store:
		return False
	settings = get_store(store)
	if settings.shopify_auth_mode != config.OAUTH_MODE:
		return False
	if not settings.enabled:
		return False
	if settings.refresh_token_expiry:
		return get_datetime(settings.refresh_token_expiry) > now_datetime()
	# A non-expiring offline token has no refresh token.
	return bool(settings.get_password("access_token", raise_exception=False)) and not settings.token_expiry


def is_paused(shop: str) -> bool:
	"""Vendor staff paused the shop's Store (``suspended``): it stays disabled until they clear it."""
	store = store_for_shop(shop)
	return bool(store and frappe.db.get_value("Store", store, "suspended"))


def ensure_offline_token(shop: str, id_token: str) -> None:
	"""
	Exchange ``id_token`` for an expiring offline token and save it as the shop's installation
	(fresh install, reinstall, or after REAUTH_REQUIRED). Commits.

	Holds the connector's token lock: an exchange and a refresh at the same time would retire
	each other's tokens. Another request that finished the exchange first wins.

	:raises InvalidSessionToken: Shopify refused the ID token (expired): the client retries.
	:raises ConnectError: The shop may not be connected here, or Shopify failed otherwise.
	"""
	try:
		with shop_token_lock(shop):
			# A fresh snapshot: another request may have just saved the tokens.
			safe_commit()
			if is_connected(shop):
				return
			if is_paused(shop):
				# No exchange: it would save tokens for a store vendor staff keep switched off,
				# on every request the merchant makes.
				raise ConnectError(
					_("Field Sales support has paused this store. Contact support to turn it back on.")
				)
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
			safe_commit()
	except TokenLockTimeout as e:
		raise ConnectError(_("Timed out waiting for another Shopify token request")) from e


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
	# A failed first exchange on the shared site has no Store yet: then an Error Log.
	log_sync_or_error(
		store=store_for_shop(shop),
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


def embedded_email(store: str) -> str:
	"""The embedded user's login: one per store on the shared site, else the site's single one."""
	if is_multi_store():
		return f"shopify-admin-{store.lower()}@{frappe.local.site}"
	return f"shopify-admin@{frappe.local.site}"


def get_embedded_user() -> str:
	"""
	The user requests from the Shopify admin run as: the Store's ``embedded_user``,
	else ``shopify-admin@<site>`` (created on first use with the Sales Manager role and its own
	Sales Rep, so the manager screens and Reports To work for it).
	"""
	store = get_store_name(required=True)
	user = get_store_value("embedded_user", store=store)
	# Checked again here, whatever the Store says (values written without validation, or older
	# than it): never Administrator, vendor staff or another store's user.
	if can_be_embedded_user(user, store) and frappe.db.get_value("User", user, "enabled"):
		return user

	email = embedded_email(store)
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
					"roles": [{"role": role} for role in EMBEDDED_ROLES],
				}
			).insert(ignore_permissions=True)
		else:
			# Disabled by shop/redact, or retired when a merchant admin took over
			# (store_admin._retire_automatic_user): the reinstalled merchant's way back in.
			_restore_automatic_user(email)
		assign_user_to_store(email, store)
		rep_name = frappe.db.get_value("Sales Rep", {"user": email}, "name")
		if rep_name:
			_reactivate_automatic_rep(rep_name)
		else:
			rep = frappe.get_doc(
				{
					"doctype": "Sales Rep",
					"user": email,
					"full_name": "Shopify Admin",
					"active": 1,
					"store": store,
				}
			)
			# Not counted against the plan (billing.usage), and embedded_user is only set below.
			rep.flags.ignore_plan_limit = True
			rep.insert(ignore_permissions=True)
		set_store_value("embedded_user", email, store)
		safe_commit()
	finally:
		frappe.set_user(previous_user)
	return email


def _restore_automatic_user(email: str) -> None:
	"""The store's automatic Shopify user, enabled and with its ``EMBEDDED_ROLES`` again."""
	user = frappe.get_doc("User", email)
	have = {row.role for row in user.roles}
	missing = [role for role in EMBEDDED_ROLES if role not in have]
	if user.enabled and not missing:
		return
	user.enabled = 1
	for role in missing:
		user.append("roles", {"role": role})
	user.save(ignore_permissions=True)


def _reactivate_automatic_rep(rep_name: str) -> None:
	"""The automatic user's Sales Rep, active and a manager again (not counted by ``billing.usage``)."""
	rep = frappe.get_doc("Sales Rep", rep_name)
	if rep.active and rep.sales_manager:
		return
	rep.update({"active": 1, "sales_manager": 1})
	rep.flags.ignore_plan_limit = True
	rep.save(ignore_permissions=True)


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
	args = request.args
	shop = (args.get("shop") or "").strip().lower()
	host = (args.get("host") or "").strip()
	id_token = args.get("id_token") or ""
	if not (shop and host and (args.get("embedded") == "1" or id_token)):
		return None
	if not config.is_valid_shop(shop) or not config.client_id():
		return None

	store = store_for_shop(shop)
	if store and get_store_value("shopify_auth_mode", store=store) == config.OAUTH_MODE:
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

		paid_store = store_for_shop(shop)
		if paid_store:
			billing.clear_cache(paid_store)

	frappe.local.shopify_frame_shop = shop
	return {
		"embedded": True,
		"shop": shop,
		"host": host,
		"shopify_api_key": config.client_id(),
	}


def _try_exchange(shop: str, id_token: str) -> None:
	"""Exchange on page load if possible; otherwise the app's first API call retries it."""
	with suppress((InvalidSessionToken, ConnectError)):
		decode(id_token, require_connected=False)
		ensure_offline_token(shop, id_token)


def after_request(response=None, request=None):
	"""
	``after_request`` hook: an embedded page may be framed by its shop's admin only
	(``frame-ancestors https://{shop} https://admin.shopify.com``), with no X-Frame-Options.
	Other responses are left alone.
	"""
	shop = getattr(frappe.local, "shopify_frame_shop", None)
	if not shop or response is None:
		return
	response.headers["Content-Security-Policy"] = f"frame-ancestors https://{shop} https://admin.shopify.com;"
	response.headers.pop("X-Frame-Options", None)


# ----------------------------------------------------------------------
# Embedded Settings
# ----------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_store_status() -> dict:
	"""Connected shop, granted scopes, locations and order mode, for managers (read-only)."""
	if not (MANAGER_ROLES & set(frappe.get_roles())) and frappe.session.user != "Administrator":
		frappe.throw(_("Only managers can see the store connection"), frappe.PermissionError)
	settings = get_store()
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
