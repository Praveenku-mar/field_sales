# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-neutral order sync: submitted Field Order -> store order.

Talks to the store only through ``get_connector()``. Each push is recorded on
the Field Order (sync_status, sync_attempts, sync_error, external ids) and in
Sync Log, and the open form is told to reload.
"""

import time

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from field_sales.connectors import ConnectorError, get_connector
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync
from field_sales.sync.customers import push_retailer
from field_sales.sync.notify import notify_sync_result

# The scheduler stops retrying an order after this many attempts; Retry Sync still works.
MAX_AUTO_ATTEMPTS = 5
# A Pending order untouched for this long lost its job (worker restart, queue down).
STALE_PENDING_MINUTES = 30


def enqueue_push_order(field_order: str):
	"""
	Queue ``push_order`` once the current transaction commits.

	Deduplicated per order, so the submit hook, the scheduler and Retry Sync
	can never run two pushes of the same order at once (which could create
	two drafts on the store).
	"""
	frappe.enqueue(
		"field_sales.sync.orders.push_order",
		field_order=field_order,
		job_id=f"field_sales:push_order:{field_order}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def push_order(field_order: str):
	"""
	Push one submitted Field Order to the store and record the outcome on it.

	Idempotent: an order already Synced is skipped, and a retry resumes from
	the draft created by an earlier attempt instead of creating another.

	A platform error (``ConnectorError``) marks the order Failed and does not
	raise, so the job itself succeeds. Any other error also marks it Failed,
	then re-raises so it shows in the job's error log.
	"""
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	if doc.docstatus != 1 or doc.sync_status == "Synced":
		return

	doc.db_set(
		{
			"sync_status": "Pending",
			"sync_attempts": cint(doc.sync_attempts) + 1,
			"last_sync_attempt": now_datetime(),
		},
		update_modified=False,
	)
	frappe.db.commit()

	platform = None
	operation = "Create Order"
	start = time.monotonic()

	try:
		conn = get_connector()
		platform = conn.settings.platform
		if conn.settings.order_mode == "Confirmed":
			operation = "Confirm Order"
		_ensure_store_customer(doc.retailer)
		result = conn.create_order(doc)
	except Exception as e:
		frappe.db.rollback()
		_reset_before_save(doc)
		message = e.message if isinstance(e, ConnectorError) else str(e)
		values = {"sync_status": "Failed", "sync_error": message}
		draft_id = getattr(e, "partial", {}).get("draft_id")
		if draft_id:
			values["external_draft_id"] = draft_id

		doc.db_set(values, update_modified=False)
		log_sync(
			platform=platform or frappe.db.get_single_value("Store Connection", "platform"),
			operation=operation,
			reference_doctype="Field Order",
			reference_name=field_order,
			status="Failed",
			attempt=doc.sync_attempts,
			duration_ms=_elapsed_ms(start),
			http_status=getattr(e, "http_status", None),
			error_code=getattr(e, "code", None) or type(e).__name__,
			error_message=message,
			external_id=draft_id,
		)
		frappe.db.commit()
		_notify(doc)
		if not isinstance(e, ConnectorError):
			raise
		return

	_reset_before_save(doc)
	doc.db_set(
		{
			"external_draft_id": result.get("draft_id"),
			"external_order_id": result.get("order_id"),
			"platform": platform,
			"location_id": conn.settings.default_location_id,
			"sync_status": "Synced",
			"sync_error": "",
		},
		update_modified=False,
	)
	log_sync(
		platform=platform,
		operation="Confirm Order" if result.get("order_id") else "Create Order",
		reference_doctype="Field Order",
		reference_name=field_order,
		status="Success",
		attempt=doc.sync_attempts,
		duration_ms=_elapsed_ms(start),
		external_id=result.get("order_id") or result.get("draft_id"),
	)
	frappe.db.commit()
	_notify(doc)


def retry_failed_orders():
	"""
	Scheduler (every 15 min): queue another push for each order that failed
	fewer than ``MAX_AUTO_ATTEMPTS`` times, and for each order stuck Pending
	because its job was lost.
	"""
	if not frappe.db.get_single_value("Store Connection", "enabled"):
		return

	failed = frappe.get_all(
		"Field Order",
		filters={"docstatus": 1, "sync_status": "Failed", "sync_attempts": ("<", MAX_AUTO_ATTEMPTS)},
		pluck="name",
	)

	stale_before = add_to_date(now_datetime(), minutes=-STALE_PENDING_MINUTES)
	stuck = frappe.get_all(
		"Field Order",
		filters={"docstatus": 1, "sync_status": "Pending", "modified": ("<", stale_before)},
		or_filters=[
			["last_sync_attempt", "is", "not set"],
			["last_sync_attempt", "<", stale_before],
		],
		pluck="name",
	)

	for name in failed + stuck:
		enqueue_push_order(name)


def _ensure_store_customer(retailer: str):
	"""Create the retailer on the store first if it is not linked to a customer yet."""
	if frappe.db.get_value("Retailer", retailer, "external_customer_id"):
		return

	push_retailer(retailer)  # commits; records its own failure on the Retailer
	if not frappe.db.get_value("Retailer", retailer, "external_customer_id"):
		error = frappe.db.get_value("Retailer", retailer, "sync_error")
		raise ConnectorError(
			_("Retailer {0} could not be created on the store: {1}").format(retailer, error or _("unknown error")),
			code="CUSTOMER_MISSING",
		)


def _notify(doc):
	"""Tell an open Field Order form and the Sync Failures page to refresh."""
	notify_sync_result("field_order_synced", "Field Order", doc.name, doc.sync_status)


def _reset_before_save(doc):
	"""
	Make the next db_set count as a fresh change for Notifications.

	The first db_set (to Pending) both snapshots the "before" values and marks
	every Notification as evaluated for this doc object. Without a reset, the
	final db_set would compare against the previous attempt's Failed (no
	change) and skip Notifications altogether, so "failed to sync" (Value
	Change on sync_status) would never fire on a retry.
	"""
	doc.load_doc_before_save(raise_exception=False)
	doc.flags.notifications_executed = []


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)
