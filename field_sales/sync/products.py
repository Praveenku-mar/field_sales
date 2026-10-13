# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-neutral product sync: store -> Platform Product.

Talks to the store only through ``get_connector()`` and the normalized product
dicts it returns, so the same code serves every platform.
"""

import hashlib
import json
import time

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from field_sales.connectors import get_connector
from field_sales.field_sales.doctype.sync_log.sync_log import log_sync

JOB_ID = "field_sales:product_sync"
REALTIME_EVENT = "product_sync_done"

# Store Connection.product_sync_frequency -> minutes between runs.
SYNC_INTERVALS = {"Every 30 minutes": 30, "Hourly": 60, "Daily": 24 * 60}
# The scheduler ticks every 30 minutes and a run's own log lands a little after
# its tick, so allow some slack or "Hourly" would only ever run every 90 minutes.
SCHEDULE_SLACK_MINUTES = 10

# Normalized product keys whose Platform Product field has a different name.
FIELD_MAP = {"image_url": "image"}


def enqueue_sync():
	"""
	Queue ``sync_products`` on the long queue.

	:return: The queued job, or ``None`` if a product sync is already queued or running.
	"""
	return frappe.enqueue(
		"field_sales.sync.products.sync_products",
		queue="long",
		timeout=1500,
		job_id=JOB_ID,
		deduplicate=True,
	)


def scheduled_sync():
	"""
	Cron entry point (every 30 minutes).

	Queues a sync when Store Connection is enabled, has a default location, and
	at least ``product_sync_frequency`` has passed since the last product sync
	activity (manual or scheduled, successful or not).
	"""
	settings = frappe.db.get_singles_dict("Store Connection")
	if not settings.enabled or not settings.default_location_id:
		return

	interval = SYNC_INTERVALS.get(settings.product_sync_frequency)
	if not interval:
		return

	last_run = frappe.db.get_value(
		"Sync Log", {"operation": "Product Sync"}, "creation", order_by="creation desc"
	)
	due_after = add_to_date(now_datetime(), minutes=-(interval - SCHEDULE_SLACK_MINUTES))
	if last_run and get_datetime(last_run) > due_after:
		return

	enqueue_sync()


def sync_products() -> dict:
	"""
	Pull every product variant from the store into Platform Product.

	- Upserts by (platform, external_variant_id); unchanged rows (same
	  ``sync_hash``) are not saved.
	- Commits after each page, so a failure keeps the pages already synced.
	- Once all pages are in, marks this platform's products that the store no
	  longer returned as Archived (skipped if the run fails part-way).
	- Writes one "Product Sync" Sync Log with the summary and publishes the
	  ``product_sync_done`` realtime event, on success and on failure.

	:return: ``{"created": n, "updated": n, "unchanged": n, "archived": n}``
	"""
	conn = get_connector()
	settings = conn.settings
	platform = settings.platform
	location_id = settings.default_location_id
	if not location_id:
		frappe.throw(_("Set a Default Location on Store Connection before syncing products"))

	summary = {"created": 0, "updated": 0, "unchanged": 0, "archived": 0}
	seen: set[str] = set()
	start = time.monotonic()

	try:
		cursor = None
		while True:
			page = conn.get_products(cursor)
			items = page["items"]

			tracked = [i["external_inventory_id"] for i in items if i["track_inventory"] and i["external_inventory_id"]]
			stock = conn.get_stock(tracked, location_id) if tracked else {}

			_upsert_page(platform, location_id, items, stock, seen, summary)
			frappe.db.commit()

			cursor = page["next_cursor"]
			if not cursor:
				break

		summary["archived"] = _archive_missing(platform, seen)
		frappe.db.commit()

	except Exception as e:
		# Drop the half-written page; earlier pages are already committed.
		frappe.db.rollback()
		message = getattr(e, "message", None) or str(e)
		log_sync(
			platform=platform,
			operation="Product Sync",
			status="Failed",
			duration_ms=_elapsed_ms(start),
			http_status=getattr(e, "http_status", None),
			error_code=getattr(e, "code", None) or type(e).__name__,
			error_message=message,
			response_payload=frappe.as_json(summary),
		)
		frappe.db.commit()
		frappe.publish_realtime(REALTIME_EVENT, {"ok": False, "message": message, **summary}, doctype="Platform Product")
		raise

	log_sync(
		platform=platform,
		operation="Product Sync",
		status="Success",
		duration_ms=_elapsed_ms(start),
		response_payload=frappe.as_json(summary),
	)
	frappe.db.commit()
	frappe.publish_realtime(REALTIME_EVENT, {"ok": True, **summary}, doctype="Platform Product")
	return summary


def _upsert_page(platform: str, location_id: str, items: list[dict], stock: dict, seen: set, summary: dict):
	"""Create or update the Platform Products for one page of normalized items."""
	variant_ids = [i["external_variant_id"] for i in items]
	existing = {
		r.external_variant_id: r
		for r in frappe.get_all(
			"Platform Product",
			filters={"platform": platform, "external_variant_id": ("in", variant_ids)},
			fields=["name", "external_variant_id", "sync_hash"],
		)
	} if variant_ids else {}
	now = now_datetime()

	for item in items:
		values = {FIELD_MAP.get(key, key): value for key, value in item.items()}
		values.update(
			platform=platform,
			track_inventory=int(bool(item["track_inventory"])),
			# Untracked items have no meaningful quantity; Field Order ignores it for them.
			available_qty=stock.get(item["external_inventory_id"], 0.0) if item["track_inventory"] else 0.0,
			location_id=location_id,
		)
		sync_hash = _hash(values)

		row = existing.get(item["external_variant_id"])
		if row:
			seen.add(row.name)
			if row.sync_hash == sync_hash:
				summary["unchanged"] += 1
				continue

		values.update(sync_hash=sync_hash, last_synced=now)
		if row:
			doc = frappe.get_doc("Platform Product", row.name)
			doc.update(values)
			doc.save(ignore_permissions=True)
			summary["updated"] += 1
		else:
			doc = frappe.get_doc({"doctype": "Platform Product", **values})
			doc.insert(ignore_permissions=True)
			seen.add(doc.name)
			summary["created"] += 1


def _archive_missing(platform: str, seen: set) -> int:
	"""Mark this platform's products that were not returned in this run as Archived."""
	filters = {"platform": platform, "status": ("!=", "Archived")}
	if seen:
		filters["name"] = ("not in", list(seen))

	stale = frappe.get_all("Platform Product", filters=filters, pluck="name")
	if stale:
		# Clearing sync_hash makes a product that comes back on the store count
		# as changed, so the next run saves it and restores its real status.
		frappe.db.set_value(
			"Platform Product",
			{"name": ("in", stale)},
			{"status": "Archived", "sync_hash": None, "last_synced": now_datetime()},
		)
	return len(stale)


def _hash(values: dict) -> str:
	"""Stable md5 of the synced values, used to skip unchanged rows."""
	return hashlib.md5(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)
