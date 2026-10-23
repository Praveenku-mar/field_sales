# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Settings of the public Shopify app, read from site_config (never stored in the database),
plus the HMAC checks Shopify uses for OAuth redirects and webhooks.

site_config keys:

- ``shopify_app_client_id`` / ``shopify_app_client_secret``: the app's credentials (Dev Dashboard).
- ``shopify_app_scopes``: comma-separated scopes asked for at install (default ``DEFAULT_SCOPES``).
- ``shopify_app_api_version``: Admin API version used in OAuth mode (default ``DEFAULT_API_VERSION``).
- ``shopify_app_url``: optional public base URL of this site (e.g. ``https://sales.example.com``),
  used for the OAuth redirect URI and webhook address; defaults to the request's address.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

import frappe
from frappe.utils import get_url

OAUTH_MODE = "OAuth"
CLIENT_CREDENTIALS_MODE = "Client Credentials"

DEFAULT_API_VERSION = "2026-10"
# Every scope the connector's queries and the subscribed webhooks need.
DEFAULT_SCOPES = (
	"read_products,read_inventory,read_locations,write_customers,read_customers,"
	"write_draft_orders,read_draft_orders,write_orders,read_orders"
)

# Shopify's documented format for a shop domain, anchored at both ends.
SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")

CALLBACK_PATH = "/api/method/field_sales.shopify.oauth.callback"
WEBHOOK_PATH = "/api/method/field_sales.shopify.webhooks.receive"
APP_HOME_PATH = "/fieldsales"


def client_id() -> str:
	return (frappe.conf.get("shopify_app_client_id") or "").strip()


def client_secret() -> str:
	return (frappe.conf.get("shopify_app_client_secret") or "").strip()


def api_version() -> str:
	return (frappe.conf.get("shopify_app_api_version") or DEFAULT_API_VERSION).strip()


def scopes() -> str:
	"""Requested scopes as Shopify wants them: comma-separated, no spaces."""
	raw = frappe.conf.get("shopify_app_scopes") or DEFAULT_SCOPES
	return ",".join(s.strip() for s in raw.split(",") if s.strip())


def missing_scopes(granted: str | None) -> list[str]:
	"""Requested scopes not covered by ``granted`` (a ``write_x`` grant covers ``read_x``)."""
	have = {s.strip() for s in (granted or "").split(",") if s.strip()}
	have |= {"read_" + s[len("write_") :] for s in have if s.startswith("write_")}
	return [s for s in scopes().split(",") if s not in have]


def is_valid_shop(shop: str | None) -> bool:
	return bool(shop) and bool(SHOP_RE.match(shop))


def public_url(path: str = "") -> str:
	"""Absolute URL of ``path`` on this site as Shopify must call it."""
	base = (frappe.conf.get("shopify_app_url") or "").strip().rstrip("/")
	if not base:
		base = get_url().rstrip("/")
	return base + path


def verify_query_hmac(args, secret: str) -> bool:
	"""
	Check the ``hmac`` of an OAuth install/callback query string.

	All other parameters, sorted by name, joined as ``key=value`` with ``&``, signed with
	HMAC-SHA256 (hex). Repeated keys (``ids[]``) are signed as ``ids=["1", "2"]``, as Shopify does.

	:param args: The request's query parameters (a werkzeug ``MultiDict`` or a plain dict).
	"""
	if not secret:
		return False
	received = args.get("hmac") or ""
	if not received:
		return False

	pairs = []
	keys = sorted(k for k in (args.keys() if hasattr(args, "keys") else args) if k != "hmac")
	for key in keys:
		values = args.getlist(key) if hasattr(args, "getlist") else [args[key]]
		if key.endswith("[]") or len(values) > 1:
			value = "[" + ", ".join(f'"{v}"' for v in values) + "]"
			key = key[:-2] if key.endswith("[]") else key
		else:
			value = values[0]
		# Shopify escapes these before signing so they can't fake a separator.
		key = key.replace("%", "%25").replace("&", "%26").replace("=", "%3D")
		value = value.replace("%", "%25").replace("&", "%26")
		pairs.append(f"{key}={value}")

	expected = hmac.new(secret.encode(), "&".join(pairs).encode(), hashlib.sha256).hexdigest()
	return hmac.compare_digest(expected.encode(), received.strip().lower().encode())


def verify_body_hmac(raw_body: bytes, signature: str | None, secrets: list[str]) -> bool:
	"""Webhook check: base64(HMAC-SHA256(secret, raw body)) must equal the header, for any of ``secrets``."""
	if not signature:
		return False
	signature = signature.strip().encode()
	for secret in secrets:
		if not secret:
			continue
		expected = base64.b64encode(hmac.new(secret.encode(), raw_body, hashlib.sha256).digest())
		if hmac.compare_digest(expected, signature):
			return True
	return False
