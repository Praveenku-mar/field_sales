# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify session tokens (App Bridge ID tokens) as a way to sign in to the API.

Inside the Shopify admin the app runs in an iframe without Frappe cookies; App Bridge sends
``Authorization: Bearer <id token>`` with every request instead. ``authenticate`` is a Frappe
``auth_hooks`` entry: it runs after Frappe's own cookie / OAuth / API key checks and, for a
request that carries a Shopify token, signs it in as Store Connection's ``embedded_user``.

A token is an HS256 JWT signed with the app's client secret, valid for one minute.
"""

from __future__ import annotations

import frappe
import jwt

from field_sales.shopify import config

LEEWAY_SECONDS = 10
RETRY_HEADER = "X-Shopify-Retry-Invalid-Session-Request"
CLIENT_CREDENTIALS_MODE = config.CLIENT_CREDENTIALS_MODE


class InvalidSessionToken(Exception):
	"""The token is not a valid session token for this site; the message is safe to log."""


def decode(token: str, require_connected: bool = True) -> dict:
	"""
	Verify a session token and return its claims, plus ``shop`` (the ``*.myshopify.com`` domain).

	Checks the HS256 signature with the app's client secret, ``exp`` / ``nbf`` (10 s leeway),
	``aud`` == client id, and that ``iss`` (minus ``/admin``) and ``dest`` are the same
	``https://{shop}``. A site whose Store Connection already points at a shop in Client
	Credentials mode never accepts one.

	A token for another shop is refused while Store Connection is enabled for a shop.

	:param require_connected: Also require the shop to be Store Connection's, in OAuth mode.
		``False`` is for the first request after install, before the token exchange saved it.
	:raises InvalidSessionToken:
	"""
	secret, client_id = config.client_secret(), config.client_id()
	if not secret or not client_id:
		raise InvalidSessionToken("The Shopify app is not configured on this site")
	try:
		claims = jwt.decode(
			token,
			secret,
			algorithms=["HS256"],
			audience=client_id,
			leeway=LEEWAY_SECONDS,
			options={"require": ["exp", "nbf", "iss", "dest", "aud"]},
		)
	except jwt.PyJWTError as e:
		raise InvalidSessionToken(f"Invalid session token ({type(e).__name__})") from e

	dest = str(claims.get("dest") or "")
	iss = str(claims.get("iss") or "")
	shop = dest.removeprefix("https://").lower()
	if (
		not dest.startswith("https://")
		or iss.removesuffix("/admin") != dest
		or not config.is_valid_shop(shop)
	):
		raise InvalidSessionToken("Session token issuer and destination do not match")

	settings = _connection()
	store = (settings.store_url or "").lower()
	if store and settings.shopify_auth_mode == CLIENT_CREDENTIALS_MODE:
		raise InvalidSessionToken("This site connects with client credentials, not the Shopify app")
	# One site, one shop: while a shop is connected, no other shop's token is accepted.
	if store and store != shop and settings.enabled:
		raise InvalidSessionToken("Session token is for another shop")
	if require_connected and (store != shop or settings.shopify_auth_mode != config.OAUTH_MODE):
		raise InvalidSessionToken("Session token is for another shop")

	claims["shop"] = shop
	return claims


def authenticate():
	"""
	``auth_hooks`` entry: sign in a request carrying a Shopify session token.

	Does nothing when someone is already signed in (cookie, API key, Frappe OAuth) or the
	request has no Shopify bearer token. On the first request after a (re)install it runs the
	token exchange (``embedded.ensure_offline_token``). A bad or expired token gets a 401 with
	``X-Shopify-Retry-Invalid-Session-Request: 1``, so App Bridge retries once with a new token.
	"""
	if frappe.session.user not in (None, "", "Guest"):
		return
	token = bearer_token()
	if not token or not _looks_like_session_token(token):
		return

	from field_sales.shopify import embedded

	# frappe.set_user() (here, and inside the install / user setup below) empties
	# frappe.local.form_dict, which holds this request's arguments; put them back after.
	form_dict = frappe.local.form_dict
	try:
		claims = decode(token, require_connected=False)
		if not embedded.is_connected(claims["shop"]):
			embedded.ensure_offline_token(claims["shop"], token)
		user = embedded.get_embedded_user()
	except InvalidSessionToken:
		frappe.local.response_headers[RETRY_HEADER] = "1"
		raise frappe.AuthenticationError
	except embedded.ConnectError as e:
		# Not something a new token fixes (e.g. another shop is connected): no retry header.
		frappe.throw(str(e), frappe.AuthenticationError)

	frappe.set_user(user)
	frappe.local.form_dict = form_dict
	# Who in Shopify made the request, for logs and audit (numeric staff id).
	frappe.local.shopify_staff_id = claims.get("sub")
	frappe.local.shopify_shop = claims["shop"]


def bearer_token() -> str | None:
	scheme, _sep, token = (frappe.get_request_header("Authorization") or "").partition(" ")
	if scheme.lower() != "bearer":
		return None
	return token.strip() or None


def _looks_like_session_token(token: str) -> bool:
	"""Cheap check before verifying: a JWT whose ``dest`` is a shop (not a Frappe OAuth token)."""
	try:
		claims = jwt.decode(token, options={"verify_signature": False})
	except jwt.PyJWTError:
		return False
	return isinstance(claims, dict) and str(claims.get("dest") or "").endswith(".myshopify.com")


def _connection():
	return frappe.get_single("Store Connection")
