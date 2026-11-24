# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-neutral order sync: submitted Field Order -> store order.

Talks to the store only through ``get_connector()``. Each push is recorded on
the Field Order (sync_status, sync_attempts, sync_error, external ids) and in
Sync Log, and the open form is told to reload.
"""

import time
from contextlib import suppress

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from field_sales.commission.engine import enqueue_create_entries
from field_sales.connectors import ConnectorError, get_connector
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync
from field_sales.push.events import notify_order_synced
from field_sales.sync.confirmation import enqueue_order_confirmation
from field_sales.sync.customers import push_retailer
from field_sales.sync.notify import notify_order_sync_failure, notify_sync_result
from field_sales.utils.db import safe_commit
from field_sales.utils.store import (
	current_store,
	enqueue_in_store,
	for_each_store,
	get_store,
	get_store_value,
	is_multi_store,
	record_store,
	store_filters,
)

# The scheduler stops retrying an order after this many attempts; Retry Sync still works.
MAX_AUTO_ATTEMPTS = 5
# A Pending order untouched for this long lost its job (worker restart, queue down).
STALE_PENDING_MINUTES = 30
# link_completed_drafts() only asks the store about drafts pushed this recently, newest first,
# at most this many a run: abandoned drafts would otherwise be re-queried every 30 minutes forever.
DRAFT_LINK_DAYS = 90
DRAFT_LINK_BATCH = 500


def enqueue_push_order(field_order: str, store: str | None = None):
	"""
	Queue ``push_order`` once the current transaction commits.

	Deduplicated per order, so the submit hook, the scheduler and Retry Sync
	can never run two pushes of the same order at once (which could create
	two drafts on the store).
	"""
	enqueue_in_store(
		"field_sales.sync.orders.push_order",
		store=store or record_store("Field Order", field_order),
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
	safe_commit()

	platform = None
	operation = "Create Order"
	start = time.monotonic()

	try:
		conn = get_connector()
		platform = conn.settings.platform
		if conn.settings.order_mode == "Confirmed" and not is_multi_store():
			operation = "Confirm Order"
		_ensure_store_customer(doc.retailer)
		result = conn.create_order(doc)

		docstatus = frappe.db.get_value("Field Order", field_order, "docstatus", for_update=True)
		if docstatus != 1:
			store_ref = (
				result.get("order_name")
				or result.get("draft_name")
				or result.get("order_id")
				or result.get("draft_id")
				or field_order
			)
			sync_error = _(
				"Cancelled while being sent; order {0} exists on the store — cancel it there"
			).format(store_ref)
			_reset_before_save(doc)
			doc.db_set(
				{
					"external_draft_id": result.get("draft_id"),
					"store_draft_name": result.get("draft_name"),
					"external_order_id": result.get("order_id"),
					"store_order_name": result.get("order_name"),
					"store_checkout_url": result.get("checkout_url"),
					"platform": platform,
					"sync_status": "Failed",
					"sync_error": sync_error,
				},
				update_modified=False,
			)
			log_sync(
				platform=platform or get_store_value("platform"),
				operation=operation,
				reference_doctype="Field Order",
				reference_name=field_order,
				status="Failed",
				attempt=doc.sync_attempts,
				duration_ms=_elapsed_ms(start),
				error_code="CANCELLED_DURING_PUSH",
				error_message=sync_error,
				external_id=result.get("order_id") or result.get("draft_id"),
			)
			safe_commit()
			_notify(doc)
			return
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
			platform=platform or get_store_value("platform"),
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
		safe_commit()
		_notify(doc)
		if not isinstance(e, ConnectorError):
			raise
		return

	_reset_before_save(doc)
	doc.db_set(
		{
			"external_draft_id": result.get("draft_id"),
			"store_draft_name": result.get("draft_name"),
			"external_order_id": result.get("order_id"),
			"store_order_name": result.get("order_name"),
			"store_checkout_url": result.get("checkout_url"),
			"platform": platform,
			# The location the rep picked stays; only an order without one gets the default.
			"location_id": doc.location_id or conn.settings.default_location_id,
			"location_name": doc.location_name or conn.settings.default_location_name,
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
	if result.get("order_id"):
		_assign_order_location(
			conn, doc, result["order_id"], doc.location_id or conn.settings.default_location_id
		)
		enqueue_create_entries(field_order)
		enqueue_order_confirmation(field_order)
		notify_order_synced(field_order)
		if doc.payment_status == "Paid":  # paid on Razorpay before the push finished
			enqueue_mark_store_paid(field_order)
	safe_commit()
	_notify(doc)


def enqueue_mark_store_paid(field_order: str, store: str | None = None):
	"""
	Queue ``mark_store_order_paid`` once the current transaction commits.

	:param store: The order's store, when the caller knows it (saves a query per order).
	"""
	enqueue_in_store(
		"field_sales.sync.orders.mark_store_order_paid",
		store=store or record_store("Field Order", field_order),
		queue="short",
		field_order=field_order,
		job_id=f"field_sales:mark_store_paid:{field_order}",
		deduplicate=True,
		enqueue_after_commit=True,
	)


def mark_store_order_paid(field_order: str):
	"""
	Mark the store order paid after the Field Order was paid in Agamsale (Razorpay, cash, bank).

	Needs a store order: a confirmed order, or a draft once it is completed on the store
	(``link_completed_drafts()`` fills ``external_order_id`` and queues this). Done once
	(``store_marked_paid_on``). A store error is in Sync Log (written by the connector) and
	retried by ``retry_store_payments()``.

	Never on a Shopify App Store install: Shopify's rules (1.1.2) do not let an app record,
	through its API, a payment taken outside Shopify checkout. There a cash / bank payment is
	recorded in Agamsale only; the merchant marks the store order paid in the Shopify admin.
	"""
	if store_payments_off():
		return
	doc = frappe.get_doc("Field Order", field_order, for_update=True)
	if (
		doc.docstatus != 1
		or doc.payment_status != "Paid"
		or not doc.external_order_id
		or doc.store_marked_paid_on
	):
		return
	try:
		conn = get_connector()
		result = conn.mark_order_paid(doc)
	except frappe.ValidationError:
		return  # store disabled; the hourly retry picks it up once it is back
	except ConnectorError as e:
		if e.code in ("NOT_FOUND", "CANNOT_MARK_PAID"):  # API errors are already logged by the connector
			log_sync(
				platform=conn.settings.platform,
				operation="Mark Order Paid",
				reference_doctype="Field Order",
				reference_name=field_order,
				status="Failed",
				error_code=e.code,
				error_message=e.message,
				external_id=doc.external_order_id,
			)
		return

	doc.db_set("store_marked_paid_on", now_datetime(), update_modified=False)
	doc.add_comment(
		"Info",
		_("Store order {0} marked as paid.").format(doc.store_order_name or doc.external_order_id)
		if not result.get("already_paid")
		else _("Store order {0} was already paid.").format(doc.store_order_name or doc.external_order_id),
	)


def store_payments_off() -> bool:
	"""A Shopify App Store install: Agamsale never marks store orders paid (see mark_store_order_paid)."""
	from field_sales.connectors.shopify import is_oauth

	settings = get_store()
	return settings.platform == "Shopify" and is_oauth(settings)


def retry_store_payments():
	"""
	Hourly, for each enabled Store: queue ``mark_store_order_paid`` again for store orders
	(confirmed, or completed drafts) paid in Agamsale in the last 7 days that the store does not
	show as paid yet (50 a run).
	"""
	for_each_store(_retry_store_payments, title="retry_store_payments")


def _retry_store_payments():
	if store_payments_off():
		return

	names = frappe.get_all(
		"Field Order",
		filters={
			**store_filters(),
			"docstatus": 1,
			"payment_status": "Paid",
			"external_order_id": ("is", "set"),
			"store_marked_paid_on": ("is", "not set"),
			"paid_on": (">=", add_to_date(now_datetime(), days=-7)),
		},
		order_by="paid_on asc",
		limit=50,
		pluck="name",
	)
	store = current_store()
	for name in names:
		enqueue_mark_store_paid(name, store)


def retry_failed_orders():
	"""
	Scheduler (every 15 min), for each enabled Store: queue another push for each order that
	failed fewer than ``MAX_AUTO_ATTEMPTS`` times, and for each order stuck Pending because its
	job was lost.
	"""
	for_each_store(_retry_failed_orders, title="retry_failed_orders")


def _retry_failed_orders():
	failed = frappe.get_all(
		"Field Order",
		filters={
			**store_filters(),
			"docstatus": 1,
			"sync_status": "Failed",
			"sync_attempts": ("<", MAX_AUTO_ATTEMPTS),
		},
		pluck="name",
	)

	stale_before = add_to_date(now_datetime(), minutes=-STALE_PENDING_MINUTES)
	stuck = frappe.get_all(
		"Field Order",
		filters={**store_filters(), "docstatus": 1, "sync_status": "Pending", "modified": ("<", stale_before)},
		or_filters=[
			["last_sync_attempt", "is", "not set"],
			["last_sync_attempt", "<", stale_before],
		],
		pluck="name",
	)

	store = current_store()
	for name in failed + stuck:
		enqueue_push_order(name, store)


def link_completed_drafts():
	"""
	Scheduler (every 30 min), for each enabled Store: fill ``external_order_id`` on Synced Field
	Orders whose draft has since been completed into an order on the store.

	``push_order`` skips Synced orders, so without this the order id of a
	draft completed on the store later would never be recorded.

	Only drafts pushed in the last ``DRAFT_LINK_DAYS`` days are checked (newest first, at most
	``DRAFT_LINK_BATCH``): a draft completed later than that is not linked automatically.
	"""
	for_each_store(_link_completed_drafts, title="link_completed_drafts")


def _link_completed_drafts():
	drafts = frappe.get_all(
		"Field Order",
		filters={
			**store_filters(),
			"docstatus": 1,
			"sync_status": "Synced",
			"external_draft_id": ("is", "set"),
			"external_order_id": ("is", "not set"),
			"last_sync_attempt": (">=", add_to_date(now_datetime(), days=-DRAFT_LINK_DAYS)),
		},
		# Everything the connector writes onto the store order, read once for the whole batch.
		fields=LINK_FIELDS,
		order_by="last_sync_attempt desc",
		limit=DRAFT_LINK_BATCH,
	)
	if not drafts:
		return

	conn = get_connector()
	completed = conn.get_completed_drafts([d.external_draft_id for d in drafts])

	for d in drafts:
		order_id = completed.get(d.external_draft_id)
		if not order_id:
			continue
		# One draft's failure must not stop the rest: the next run would list the same draft
		# first again (newest attempt first) and block every draft after it.
		try:
			if _link_completed_draft(conn, d, order_id):
				safe_commit()
				notify_sync_result("field_order_synced", "Field Order", d.name, "Synced")
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Linking completed draft failed: {d.name}")


# What _link_completed_draft needs of a Field Order (and what the connector writes onto the order).
LINK_FIELDS = ["name", "external_draft_id", "sales_rep", "location_id", "location_name"]


def _link_completed_draft(conn, d, order_id: str) -> bool:
	"""
	Record the store order a Field Order's draft became, and do what an order id starts: order
	fields and location on the store, the payment taken at checkout, commission, the confirmation
	email and the rep's notification. Does not commit.

	The row is locked first, so the draft linker and an order webhook cannot both link it (which
	would send the confirmation twice).

	:param d: The Field Order's ``LINK_FIELDS``; ``doctype`` is set here for the connector's logs.
	:return: ``False`` if it was already linked.
	"""
	if frappe.db.get_value("Field Order", d.name, "external_order_id", for_update=True):
		return False
	# The row stands in for the Field Order: the connector logs its call against
	# ``doctype`` / ``name`` (a Sync Log needs both).
	d.doctype = "Field Order"
	frappe.db.set_value("Field Order", d.name, "external_order_id", order_id, update_modified=False)
	# Logged by the connector; the order itself is linked either way.
	with suppress(ConnectorError):
		# Shopify does not copy a draft's fields to the order made from it.
		conn.set_order_metafields(order_id, d)
	# The checkout or admin completion routed it by the store's own rules.
	_assign_order_location(conn, d, order_id, d.location_id)
	_carry_draft_payment(conn, d.name, order_id)
	enqueue_create_entries(d.name)
	enqueue_order_confirmation(d.name)
	log_sync(
		platform=conn.settings.platform,
		operation="Confirm Order",
		reference_doctype="Field Order",
		reference_name=d.name,
		status="Success",
		external_id=order_id,
	)
	notify_order_synced(d.name)
	return True


def link_order_from_event(conn, external_order_id: str, payload: dict) -> str | None:
	"""
	An order event for a store order no Field Order holds yet: if it is the order a Field Order's
	draft was just completed into, link it now instead of waiting for ``link_completed_drafts``.

	The payload only names a candidate (``connector.field_order_from_order_event``); the store must
	confirm that this Field Order's own draft became exactly this order, so an edited note cannot
	link someone else's order. Does not commit.

	:return: The Field Order linked, or ``None``.
	"""
	if not external_order_id or frappe.db.exists("Field Order", {"external_order_id": external_order_id}):
		return None
	name = conn.field_order_from_order_event(payload or {})
	if not name:
		return None
	d = frappe.db.get_value(
		"Field Order",
		{
			**store_filters(),
			"name": name,
			"docstatus": 1,
			"sync_status": "Synced",
			"external_draft_id": ("is", "set"),
		},
		LINK_FIELDS,
		as_dict=True,
	)
	if not d:
		return None
	if conn.get_completed_drafts([d.external_draft_id]).get(d.external_draft_id) != str(external_order_id):
		return None
	if not _link_completed_draft(conn, d, str(external_order_id)):
		return None
	notify_sync_result("field_order_synced", "Field Order", d.name, "Synced")
	return d.name


def _assign_order_location(conn, field_order, order_id: str, location_id: str | None) -> None:
	"""
	Best effort: commit the store order's stock at the location the rep sold from
	(``connector.assign_order_location``). The order is synced either way; a refusal (e.g. the
	app lacks the fulfilment-order scope) is in the connector's Sync Log, anything else in the
	Error Log.
	"""
	if not location_id:
		return
	try:
		conn.assign_order_location(order_id, location_id, field_order)
	except ConnectorError:
		# Not lost: every failed store call (and token refresh) already wrote a Failed Sync Log
		# for this order (connectors/shopify.py -> _graphql), which the sync monitor shows.
		return
	except Exception:
		frappe.log_error(title=f"Assigning the store order location failed: {field_order.name}")


def _carry_draft_payment(conn, field_order: str, order_id: str):
	"""
	A draft just became a store order: bring its payment across, whichever side took it.

	- Paid on the store (the draft's checkout link, any gateway): the store's paid webhook came
	  before the order was linked and was ignored, so read the payment now. Safe to repeat.
	- Paid in Agamsale (Razorpay, cash, bank): mark the new store order paid (not on App Store
	  installs, see ``mark_store_order_paid``).
	"""
	from field_sales.payments.store_payments import apply_store_payment

	# Logged by the connector; the order is linked either way.
	with suppress(ConnectorError):
		apply_store_payment(conn, order_id)
	paid = frappe.db.get_value(
		"Field Order", field_order, ["payment_status", "store_marked_paid_on"], as_dict=True
	)
	if paid.payment_status == "Paid" and not paid.store_marked_paid_on:
		enqueue_mark_store_paid(field_order)


def _ensure_store_customer(retailer: str):
	"""Create the retailer on the store first if it is not linked to a customer yet."""
	if frappe.db.get_value("Retailer", retailer, "external_customer_id"):
		return

	push_retailer(retailer)  # commits; records its own failure on the Retailer
	if not frappe.db.get_value("Retailer", retailer, "external_customer_id"):
		error = frappe.db.get_value("Retailer", retailer, "sync_error")
		raise ConnectorError(
			_("Retailer {0} could not be created on the store: {1}").format(
				retailer, error or _("unknown error")
			),
			code="CUSTOMER_MISSING",
		)


def _notify(doc):
	"""Tell an open Field Order form and the Sync Failures page to refresh."""
	notify_sync_result("field_order_synced", "Field Order", doc.name, doc.sync_status)
	if doc.sync_status == "Failed":
		notify_order_sync_failure(doc)


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
