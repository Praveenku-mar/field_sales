# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify App Pricing (formerly managed pricing): the merchant picks a plan on Shopify's own plan
page; this app never creates charges. Prices live in the Partner Dashboard, not in code.

- ``get_active_plan()``: the shop's plan handle from the Partner API ``activeSubscription``
  (Shopify's documented way to read it), cached for an hour in Redis. Needs site_config
  ``shopify_partner_org_id``, ``shopify_partner_api_token`` and ``shopify_app_gid``
  (``gid://shopify/App/<id>``); without them the plan is unknown and nothing is limited.
- ``check_limit(kind)``: ``field_sales_limit_checks`` hook (see ``utils/limits.py``); limits come
  from Store Connection ``plan_limits`` ({plan handle: {"sales_reps": n, "orders_per_month": n}},
  key ``"none"`` = no active plan). Client Credentials sites are never limited.
- ``pricing_url()``: ``https://admin.shopify.com/store/{store}/charges/{app}/pricing_plans``.

Plan changes: since April 28, 2026 Shopify sends no subscription webhooks for App Pricing;
it appends ``plan_handle`` to the welcome link instead (``embedded.page_context`` then calls
``clear_cache()``), and the cache expires within the hour otherwise.
"""

from __future__ import annotations

import json

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_first_day, get_last_day, nowdate

from field_sales.connectors.shopify import REQUEST_TIMEOUT, is_oauth
from field_sales.utils.limits import ORDERS_PER_MONTH, SALES_REPS

CACHE_KEY = "field_sales:shopify_active_plan"
CACHE_SECONDS = 60 * 60
NO_PLAN = "none"
PARTNER_API_VERSION = "2026-10"

ACTIVE_SUBSCRIPTION_QUERY = """
query($appId: ID!, $shopId: ID!) {
  activeSubscription(appId: $appId, shopId: $shopId) { trialEndsAt items { handle } }
}"""


def get_active_plan() -> dict:
	"""
	``{"plan": handle | None, "trial_ends_at": str | None, "known": bool}``.

	``known`` is False when the Partner API isn't configured or failed: limits are then not
	enforced (a lookup problem must not stop reps from working).
	"""
	cached = frappe.cache.get_value(CACHE_KEY)
	if cached:
		return cached
	result = _fetch_plan()
	if result["known"]:
		frappe.cache.set_value(CACHE_KEY, result, expires_in_sec=CACHE_SECONDS)
	return result


def clear_cache() -> None:
	frappe.cache.delete_value(CACHE_KEY)


def _fetch_plan() -> dict:
	unknown = {"plan": None, "trial_ends_at": None, "known": False}
	org_id = (frappe.conf.get("shopify_partner_org_id") or "").strip()
	token = (frappe.conf.get("shopify_partner_api_token") or "").strip()
	app_gid = (frappe.conf.get("shopify_app_gid") or "").strip()
	if not (org_id and token and app_gid):
		return unknown

	from field_sales.connectors import get_connector

	try:
		# Also while disabled (e.g. waiting for a reinstall): the plan is the shop's either way.
		shop_gid = get_connector(require_enabled=False).get_shop_id()
		response = requests.post(
			f"https://partners.shopify.com/{org_id}/api/{PARTNER_API_VERSION}/graphql.json",
			json={"query": ACTIVE_SUBSCRIPTION_QUERY, "variables": {"appId": app_gid, "shopId": shop_gid}},
			headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
			timeout=REQUEST_TIMEOUT,
		)
		body = response.json() if response.ok else {}
	except Exception as e:
		frappe.log_error(title="Shopify plan lookup failed", message=f"{type(e).__name__}: {e}")
		return unknown
	if not response.ok or body.get("errors"):
		frappe.log_error(
			title="Shopify plan lookup failed",
			message=f"HTTP {response.status_code}: {json.dumps(body.get('errors'))[:1000]}",
		)
		return unknown

	subscription = (body.get("data") or {}).get("activeSubscription")
	if not subscription:
		return {"plan": None, "trial_ends_at": None, "known": True}
	handles = [i.get("handle") for i in subscription.get("items") or [] if i.get("handle")]
	return {
		"plan": handles[0] if handles else None,
		"trial_ends_at": subscription.get("trialEndsAt"),
		"known": True,
	}


def pricing_url() -> str | None:
	"""Shopify's plan selection page for this shop and app, or ``None`` if the app handle is unknown."""
	store = (frappe.db.get_single_value("Store Connection", "store_url") or "").lower()
	app_handle = (frappe.conf.get("shopify_app_handle") or "").strip()
	if not store.endswith(".myshopify.com") or not app_handle:
		return None
	return f"https://admin.shopify.com/store/{store.removesuffix('.myshopify.com')}/charges/{app_handle}/pricing_plans"


def plan_limits() -> dict:
	raw = frappe.db.get_single_value("Store Connection", "plan_limits")
	if not raw:
		return {}
	try:
		value = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		return {}
	return value if isinstance(value, dict) else {}


def check_limit(kind: str) -> None:
	"""``field_sales_limit_checks`` hook: refuse one more ``kind`` when the plan's limit is reached."""
	settings = frappe.get_single("Store Connection")
	if settings.platform != "Shopify" or not is_oauth(settings):
		return
	limits = plan_limits()
	if not limits:
		return
	plan = get_active_plan()
	if not plan["known"]:
		return
	plan_name = plan["plan"] or NO_PLAN
	limit = (limits.get(plan_name) or {}).get(kind)
	if limit in (None, ""):
		return
	limit = cint(limit)
	used = usage(kind)
	if used < limit:
		return

	what = {SALES_REPS: _("active sales reps"), ORDERS_PER_MONTH: _("submitted orders this month")}[kind]
	message = _("Your Field Sales plan ({0}) allows {1} {2}, and {3} are in use.").format(
		plan_name, limit, what, used
	)
	url = pricing_url()
	if url:
		message += " " + _('<a href="{0}" target="_top">Change your plan in Shopify</a>.').format(url)
	frappe.throw(message, title=_("Plan limit reached"))


def usage(kind: str) -> int:
	"""How many of ``kind`` are used now (the embedded app's own Sales Rep is not counted)."""
	if kind == SALES_REPS:
		embedded_user = frappe.db.get_single_value("Store Connection", "embedded_user")
		filters = {"active": 1}
		if embedded_user:
			filters["user"] = ("!=", embedded_user)
		return frappe.db.count("Sales Rep", filters)
	if kind == ORDERS_PER_MONTH:
		today = nowdate()
		return frappe.db.count(
			"Field Order",
			{"docstatus": 1, "order_date": ("between", [get_first_day(today), get_last_day(today)])},
		)
	raise ValueError(kind)


@frappe.whitelist(methods=["GET"])
def get_plan_info() -> dict:
	"""Settings page: the shop's plan, limits and usage (managers; OAuth mode only)."""
	if (
		not ({"System Manager", "Sales Manager"} & set(frappe.get_roles()))
		and frappe.session.user != "Administrator"
	):
		frappe.throw(_("Only managers can see the plan"), frappe.PermissionError)
	settings = frappe.get_single("Store Connection")
	if settings.platform != "Shopify" or not is_oauth(settings):
		return {"oauth": False}
	plan = get_active_plan()
	limits = plan_limits().get(plan["plan"] or NO_PLAN) or {}
	return {
		"oauth": True,
		"known": plan["known"],
		"plan": plan["plan"],
		"trial_ends_at": plan["trial_ends_at"],
		"pricing_url": pricing_url(),
		"limits": limits,
		"usage": {kind: usage(kind) for kind in (SALES_REPS, ORDERS_PER_MONTH)},
	}
