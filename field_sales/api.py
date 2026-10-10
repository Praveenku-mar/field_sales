# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Whitelisted endpoints used by the Store Connection form and the Platform Product / Retailer views."""

import frappe
from frappe import _
from frappe.utils import now_datetime

from field_sales.connectors import ConnectorError, get_connector
from field_sales.sync.products import enqueue_sync


@frappe.whitelist(methods=["POST"])
def test_connection() -> dict:
	"""
	Test the configured store connection and record the outcome on Store Connection.

	:return: ``{"ok": bool, "shop_name": str, "message": str}``
	"""
	frappe.only_for("System Manager")

	result = get_connector().test_connection()
	status = "Success" if result.get("ok") else f"Failed: {result.get('message')}"
	frappe.db.set_single_value(
		"Store Connection",
		{"last_test_status": status[:140], "last_tested_on": now_datetime()},
	)
	return result


@frappe.whitelist()
def get_locations() -> list[dict]:
	"""
	List the store's active locations for the "Fetch Locations" picker.

	:return: ``[{"id": str, "name": str}, ...]``
	"""
	frappe.only_for("System Manager")

	try:
		return get_connector().get_locations()
	except ConnectorError as e:
		frappe.throw(e.message, title=_("Could not fetch locations"))


@frappe.whitelist(methods=["POST"])
def enqueue_product_sync() -> dict:
	"""
	Start a background product sync (the "Sync Products" button).

	Fails fast if Store Connection is disabled or has no default location, so
	the user sees the problem now rather than in a failed job.

	:return: ``{"queued": bool, "message": str}``; ``queued`` is false if a sync is already running.
	"""
	frappe.only_for(["System Manager", "Sales Manager"])

	get_connector()
	if not frappe.db.get_single_value("Store Connection", "default_location_id"):
		frappe.throw(_("Set a Default Location on Store Connection before syncing products"))

	if enqueue_sync():
		return {"queued": True, "message": _("Sync started")}
	return {"queued": False, "message": _("A product sync is already running")}
