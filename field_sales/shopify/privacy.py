# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Privacy law compliance jobs for Shopify's mandatory webhooks (queued by ``webhooks.receive``).

- ``customers/data_request``: JSON export of what we hold about the customer (Retailer,
  visits, Field Orders), saved as a private File on the Privacy Request; System Managers
  are emailed to pass it to the store owner.
- ``customers/redact``: anonymise the customer's Retailer(s), the personal fields of their
  Field Orders and Visits; amounts, quantities and dates stay for reporting.
- ``shop/redact`` (48 hours after uninstall): anonymise every Retailer, Field Order and Visit,
  delete Platform Products and stock rows, blank Sync Log bodies, drop tokens and exports.

A Retailer belongs to a Shopify customer through ``external_customer_id``. Updates go
straight to the database: no controllers run, so nothing is pushed back to the store.
Edit history (Version) and emails (Communication) of redacted records are deleted, since
they hold the old values. The Privacy Request keeps only ids and counts.
"""

from __future__ import annotations

import frappe
from frappe.utils import escape_html, get_url_to_form, now_datetime

from field_sales.connectors.shopify import clear_oauth_tokens
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync, log_sync_durable
from field_sales.sync.notify import users_with_roles
from field_sales.utils.anonymise import anonymise as _redact

RETAILER_EXPORT_FIELDS = [
	"name", "retailer_name", "owner_name", "phone", "email", "gstin", "retailer_type", "status",
	"address_line1", "address_line2", "city", "state", "pincode", "latitude", "longitude",
	"external_customer_id", "creation",
]  # fmt: skip
VISIT_EXPORT_FIELDS = [
	"name", "retailer", "visit_date", "status", "visit_purpose", "check_in_time", "check_out_time",
	"notes", "order_taken", "no_order_reason", "no_order_remarks",
]  # fmt: skip
ORDER_EXPORT_FIELDS = [
	"name", "retailer", "retailer_name", "order_date", "delivery_date", "external_order_id",
	"store_order_name", "net_total", "tax_amount", "discount_amount", "grand_total", "remarks",
	"payment_status", "amount_paid", "invoice_no", "docstatus",
]  # fmt: skip
ITEM_EXPORT_FIELDS = ["parent", "product_title", "variant_title", "sku", "qty", "rate", "amount"]


def process(privacy_request: str, payload: dict):
	"""Run the handler for the request's topic and record the outcome (Completed / Failed)."""
	doc = frappe.get_doc("Privacy Request", privacy_request)
	handler = {
		"customers/data_request": data_request,
		"customers/redact": customer_redact,
		"shop/redact": shop_redact,
	}[doc.topic]
	try:
		details = handler(doc, payload or {})
	except Exception as e:
		frappe.db.rollback()
		frappe.db.set_value(
			"Privacy Request",
			doc.name,
			{"status": "Failed", "details": frappe.as_json({"error": f"{type(e).__name__}: {e}"[:500]})},
		)
		log_sync_durable(
			platform="Shopify",
			operation="Privacy Request",
			status="Failed",
			reference_doctype="Privacy Request",
			reference_name=doc.name,
			error_code=type(e).__name__,
			error_message=f"{doc.topic}: {e}"[:500],
		)
		frappe.db.commit()
		raise

	frappe.db.set_value(
		"Privacy Request",
		doc.name,
		{"status": "Completed", "completed_on": now_datetime(), "details": frappe.as_json(details)},
	)
	log_sync(
		platform="Shopify",
		operation="Privacy Request",
		status="Success",
		reference_doctype="Privacy Request",
		reference_name=doc.name,
		response_payload=frappe.as_json({"topic": doc.topic, **details}),
	)
	return details


# ----------------------------------------------------------------------
# Topics
# ----------------------------------------------------------------------


def data_request(doc, payload: dict) -> dict:
	"""Export the customer's data to a private JSON File on ``doc`` and email System Managers."""
	retailers = _retailers_for(payload)
	order_ids = _ids(payload.get("orders_requested"))
	orders = _orders_for(retailers, order_ids)

	export = {
		"shop_domain": doc.shop_domain,
		"shopify_customer_id": doc.shopify_customer_id,
		"data_request_id": doc.request_id,
		"generated_on": str(now_datetime()),
		"retailers": frappe.get_all(
			"Retailer", filters={"name": ("in", retailers)}, fields=RETAILER_EXPORT_FIELDS
		)
		if retailers
		else [],
		"visits": frappe.get_all(
			"Visit",
			filters={"retailer": ("in", retailers)},
			fields=VISIT_EXPORT_FIELDS,
			order_by="visit_date",
		)
		if retailers
		else [],
		"field_orders": _export_orders(orders),
	}
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"privacy-export-{doc.name}.json",
			"attached_to_doctype": "Privacy Request",
			"attached_to_name": doc.name,
			"is_private": 1,
			"content": frappe.as_json(export),
		}
	).insert(ignore_permissions=True)
	# A personal-data export: on record in Frappe's Access Log, like a report export.
	from frappe.core.doctype.access_log.access_log import make_access_log

	make_access_log(
		doctype="Privacy Request",
		document=doc.name,
		method="field_sales.shopify.privacy.data_request",
		file_type="json",
		columns="retailers, visits, field_orders",
	)

	_email_system_managers(doc)
	return {
		"retailers": retailers,
		"field_orders": len(orders),
		"visits": len(export["visits"]),
		"file": file.name,
	}


def customer_redact(doc, payload: dict) -> dict:
	"""Anonymise the customer's Retailer(s) and the personal fields of their orders and visits."""
	retailers = _retailers_for(payload)
	orders = _orders_for(retailers, _ids(payload.get("orders_to_redact")))
	return _redact(retailers, orders)


def shop_redact(doc, payload: dict) -> dict:
	"""The store left 48 hours ago: anonymise or delete everything that came from it."""
	retailers = frappe.get_all("Retailer", pluck="name")
	orders = frappe.get_all("Field Order", pluck="name")
	result = _redact(retailers, orders)

	result["platform_products"] = frappe.db.count("Platform Product")
	result["product_stock"] = frappe.db.count("Product Stock")
	frappe.db.delete("Product Stock")
	frappe.db.delete("Platform Product")

	sync_log = frappe.qb.DocType("Sync Log")
	(
		frappe.qb.update(sync_log)
		.set(sync_log.request_payload, None)
		.set(sync_log.response_payload, None)
		.run()
	)
	result["sync_log_bodies_cleared"] = frappe.db.count("Sync Log")

	exports = frappe.get_all("File", filters={"attached_to_doctype": "Privacy Request"}, pluck="name")
	for name in exports:
		frappe.delete_doc("File", name, ignore_permissions=True, force=True)
	result["exports_deleted"] = len(exports)

	clear_oauth_tokens()
	frappe.db.set_single_value("Store Connection", {"enabled": 0, "shopify_scopes": None})
	result["tokens_cleared"] = True
	return result


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _ids(values) -> list[str]:
	return [str(v) for v in (values or []) if v not in (None, "")]


def _retailers_for(payload: dict) -> list[str]:
	"""Retailers linked to the payload's Shopify customer id."""
	customer_id = (payload.get("customer") or {}).get("id")
	if not customer_id:
		return []
	return frappe.get_all("Retailer", filters={"external_customer_id": str(customer_id)}, pluck="name")


def _orders_for(retailers: list[str], order_ids: list[str]) -> list[str]:
	"""Field Orders of ``retailers`` plus those whose store order id is in ``order_ids``."""
	names = set()
	if retailers:
		names.update(frappe.get_all("Field Order", filters={"retailer": ("in", retailers)}, pluck="name"))
	if order_ids:
		names.update(
			frappe.get_all("Field Order", filters={"external_order_id": ("in", order_ids)}, pluck="name")
		)
	return sorted(names)


def _export_orders(orders: list[str]) -> list[dict]:
	if not orders:
		return []
	rows = frappe.get_all(
		"Field Order", filters={"name": ("in", orders)}, fields=ORDER_EXPORT_FIELDS, order_by="order_date"
	)
	items = frappe.get_all(
		"Field Order Item",
		filters={"parent": ("in", orders), "parenttype": "Field Order"},
		fields=ITEM_EXPORT_FIELDS,
		order_by="idx",
	)
	by_order = {}
	for item in items:
		by_order.setdefault(item.pop("parent"), []).append(item)
	for row in rows:
		row["items"] = by_order.get(row.name, [])
	return rows


def _email_system_managers(doc) -> None:
	"""Tell System Managers a data export is ready to send to the store owner (link only, no data)."""
	recipients = [u for u in users_with_roles(("System Manager",)) if u not in ("Administrator", "Guest")]
	if not recipients:
		return
	link = get_url_to_form("Privacy Request", doc.name)
	frappe.sendmail(
		recipients=recipients,
		subject=f"Customer data request from {doc.shop_domain}",
		message=(
			f"<p>Shopify customer {escape_html(doc.shopify_customer_id or '')} asked for the data "
			f"{escape_html(doc.shop_domain or '')} holds about them in Field Sales.</p>"
			f'<p>The export is attached to <a href="{escape_html(link)}">{escape_html(doc.name)}</a>. '
			"Send it to the store owner within 30 days of the request.</p>"
		),
		reference_doctype="Privacy Request",
		reference_name=doc.name,
	)
