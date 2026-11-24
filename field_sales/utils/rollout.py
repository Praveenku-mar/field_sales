# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Readiness checks for the shared multi-store site (Phase 9 rollout).

``shared_site_checks()`` is what ``bench --site <site> field-sales-check-shared`` prints: run it
before opening the site to merchants and after every deploy (docs/multi-merchant/rollout.md).
Each check is ``(level, label, detail)`` with level ``ok``, ``warn`` or ``fail``; any ``fail``
means the site must not take installs yet.
"""

from __future__ import annotations

import frappe

OK, WARN, FAIL = "ok", "warn", "fail"


def shared_site_checks() -> list[tuple[str, str, str]]:
	checks = []
	checks += _site_checks()
	checks += _app_checks()
	checks += _staff_checks()
	checks += _store_checks()
	return checks


def has_failures(checks) -> bool:
	return any(level == FAIL for level, _label, _detail in checks)


# ----------------------------------------------------------------------
# Site
# ----------------------------------------------------------------------


def _site_checks() -> list:
	from frappe.utils.scheduler import is_scheduler_disabled

	from field_sales.utils.store import is_multi_store

	checks = [
		(
			(OK, "Shared site flag", "field_sales_multi_store is on")
			if is_multi_store()
			else (FAIL, "Shared site flag", "set site_config field_sales_multi_store = 1")
		),
		(
			(FAIL, "Scheduler", "the scheduler is disabled: syncs, retries and summaries would not run")
			if is_scheduler_disabled(verbose=False)
			else (OK, "Scheduler", "enabled")
		),
	]
	zone = frappe.db.get_single_value("System Settings", "time_zone")
	checks.append(
		(OK, "Site clock", f"System Settings time zone {zone}; never change it once stores exist")
		if zone
		else (WARN, "Site clock", "set System Settings time zone once, before the first store (it is never changed later)")
	)
	from frappe.email.doctype.email_account.email_account import EmailAccount

	account = EmailAccount.find_default_outgoing()
	checks.append(
		(OK, "Outgoing email", f"{account.email_id} sends every store's mail in the store's name")
		if account and account.get("email_id")
		else (WARN, "Outgoing email", "add a default outgoing Email Account (SPF/DKIM on the vendor domain)")
	)
	if frappe.db.get_single_value("Razorpay Settings", "enabled"):
		checks.append((WARN, "Razorpay", "enabled in Razorpay Settings but always off on the shared site"))
	return checks


# ----------------------------------------------------------------------
# The Shopify app
# ----------------------------------------------------------------------


def _app_checks() -> list:
	from field_sales.shopify import config

	checks = []
	for key, label in (("shopify_app_client_id", "App client id"), ("shopify_app_client_secret", "App client secret")):
		checks.append(
			(OK, label, f"{key} is set") if frappe.conf.get(key) else (FAIL, label, f"set site_config {key}")
		)
	try:
		url = config.public_url(config.CALLBACK_PATH)
		checks.append((OK, "Public address", f"Shopify calls back to {url}"))
	except Exception:
		checks.append((FAIL, "Public address", "set site_config shopify_app_url to the site's https:// address"))
	billing = ("shopify_partner_org_id", "shopify_partner_api_token", "shopify_app_gid")
	missing = [k for k in billing if not frappe.conf.get(k)]
	checks.append(
		(WARN, "Billing lookup", f"missing {', '.join(missing)}: plans show as unknown")
		if missing
		else (OK, "Billing lookup", "Partner API keys set")
	)
	if not frappe.conf.get("shopify_app_handle"):
		checks.append((WARN, "Plan page link", "set shopify_app_handle so merchants can open the pricing page"))
	return checks


# ----------------------------------------------------------------------
# Vendor staff
# ----------------------------------------------------------------------


def _staff_checks() -> list:
	"""
	Who sees every store: only users of no store holding ``VENDOR_ROLE`` (and Administrator).
	A System Manager of no store without it reaches no store; a store's user holding a site-wide
	role would act beyond its store in Desk.
	"""
	from field_sales.utils.store import SITE_WIDE_ROLES, VENDOR_ROLE, user_store

	if not frappe.db.exists("Role", VENDOR_ROLE):
		return [(FAIL, "Vendor role", f"role {VENDOR_ROLE} is missing: run bench migrate")]

	holders: dict[str, set[str]] = {}
	for row in frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ("in", tuple(SITE_WIDE_ROLES))},
		fields=["parent", "role"],
	):
		holders.setdefault(row.parent, set()).add(row.role)
	enabled = set(
		frappe.get_all(
			"User",
			filters={"name": ("in", list(holders) or [""]), "enabled": 1},
			pluck="name",
		)
	)
	vendors, roleless, in_store = [], [], []
	for user in sorted(enabled - {"Administrator", "Guest"}):
		if user_store(user):
			in_store.append(user)
		elif VENDOR_ROLE in holders[user]:
			vendors.append(user)
		else:
			roleless.append(user)

	checks = [
		(OK, "Vendor staff", f"{len(vendors)}: {', '.join(vendors)}")
		if vendors
		else (WARN, "Vendor staff", f"nobody holds {VENDOR_ROLE}: only Administrator can support stores")
	]
	if roleless:
		checks.append(
			(
				WARN,
				"System Managers in no store",
				f"{', '.join(roleless)} reach no store: give {VENDOR_ROLE} to vendor staff",
			)
		)
	if in_store:
		checks.append(
			(
				FAIL,
				"Store users with site-wide roles",
				f"{', '.join(in_store)}: remove System Manager / {VENDOR_ROLE} from a store's users",
			)
		)
	return checks


# ----------------------------------------------------------------------
# Stores
# ----------------------------------------------------------------------


def _store_checks() -> list:
	stores = frappe.get_all(
		"Store",
		fields=[
			"name",
			"store_url",
			"enabled",
			"uninstalled_on",
			"shop_currency",
			"shop_timezone",
			"default_location_id",
			"shopify_auth_mode",
		],
		order_by="creation asc",
	)
	if not stores:
		return [(OK, "Stores", "none yet: each Shopify install creates its own")]
	active = [s for s in stores if s.enabled and not s.uninstalled_on]
	checks = [
		(
			OK,
			"Stores",
			f"{len(stores)} in all, {len(active)} active, "
			f"{sum(1 for s in stores if s.uninstalled_on)} uninstalled",
		)
	]
	for s in active:
		gaps = []
		if s.shopify_auth_mode != "OAuth":
			gaps.append("not an App Store (OAuth) install")
		if not s.shop_currency or not s.shop_timezone:
			gaps.append("shop currency / time zone not synced yet")
		if not s.default_location_id:
			gaps.append("no default location (products do not sync)")
		if gaps:
			checks.append((WARN, f"Store {s.name}", f"{s.store_url}: " + "; ".join(gaps)))
	return checks
