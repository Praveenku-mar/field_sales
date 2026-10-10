# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Whitelisted endpoints used by the Store Connection, Retailer and Visit forms and the Platform Product view."""

import frappe
from frappe import _
from frappe.utils import flt, get_link_to_form, now_datetime

from field_sales.connectors import ConnectorError, get_connector
from field_sales.field_sales.doctype.visit.visit import get_geofence_radius
from field_sales.sync.customers import push_retailers
from field_sales.sync.products import enqueue_sync
from field_sales.utils.geo import has_coords, haversine_m, is_valid_coords, point_geojson

VISIT_MANAGER_ROLES = {"Sales Manager", "System Manager"}


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


@frappe.whitelist(methods=["POST"])
def sync_retailers(names: str | list) -> dict:
	"""
	Queue a push to the store for each given Retailer ("Sync to Store").

	:param names: Retailer names (list, or JSON list from the client).
	:return: ``{"message": str}``
	"""
	names = frappe.parse_json(names) if isinstance(names, str) else names
	if not names:
		frappe.throw(_("Select at least one Retailer"))

	for name in names:
		frappe.has_permission("Retailer", "write", name, throw=True)

	get_connector()  # fails fast if Store Connection is disabled
	push_retailers(names)
	return {"message": _("Sync to store queued for {0} retailer(s)").format(len(names))}


@frappe.whitelist(methods=["POST"])
def check_in(visit: str, latitude: float, longitude: float, accuracy: float | None = None) -> dict:
	"""
	Check in to a Planned visit at the rep's current GPS position.

	Time comes from the server, never the client. The first check-in at a
	retailer with no coordinates sets the shop's location. Checking in outside
	the geofence is allowed, but returns a warning.

	:return: ``{"status", "distance_m", "within_geofence", "warning", "retailer_location_set"}``
	"""
	latitude, longitude, accuracy = _parse_position(latitude, longitude, accuracy)

	doc = frappe.get_doc("Visit", visit, for_update=True)
	_check_visit_access(doc)
	if doc.status != "Planned":
		frappe.throw(_("Only a Planned visit can be checked in to. This visit is {0}.").format(_(doc.status)))

	# Lock the rep so two tabs / devices cannot both open a visit at once.
	frappe.db.get_value("Sales Rep", doc.sales_rep, "name", for_update=True)
	open_visit = frappe.db.get_value(
		"Visit",
		{"sales_rep": doc.sales_rep, "status": "In Progress", "name": ("!=", doc.name)},
		"name",
	)
	if open_visit:
		frappe.throw(
			_("Check out of {0} before checking in to another visit.").format(
				get_link_to_form("Visit", open_visit)
			),
			title=_("Visit Already In Progress"),
		)

	retailer = frappe.db.get_value(
		"Retailer", doc.retailer, ["latitude", "longitude"], as_dict=True, for_update=True
	)
	retailer_location_set = not has_coords(retailer.latitude, retailer.longitude)
	if retailer_location_set:
		# First visit to a shop with no location on file: this check-in becomes the shop's location.
		frappe.db.set_value(
			"Retailer",
			doc.retailer,
			{
				"latitude": latitude,
				"longitude": longitude,
				"location_map": point_geojson(latitude, longitude),
			},
		)
		retailer.latitude, retailer.longitude = latitude, longitude

	doc.update(
		{
			"check_in_time": now_datetime(),
			"latitude": latitude,
			"longitude": longitude,
			"location_accuracy_m": accuracy,
			"retailer_latitude": retailer.latitude,
			"retailer_longitude": retailer.longitude,
			"status": "In Progress",
		}
	)
	# Visit.validate computes distance_from_retailer_m and within_geofence.
	doc.flags.via_check_in_api = True
	doc.save()

	warning = None
	if not doc.within_geofence:
		warning = _("You are {0} m away from the retailer").format(round(doc.distance_from_retailer_m))

	return {
		"status": doc.status,
		"distance_m": doc.distance_from_retailer_m,
		"within_geofence": doc.within_geofence,
		"warning": warning,
		"retailer_location_set": retailer_location_set,
	}


@frappe.whitelist(methods=["POST"])
def check_out(visit: str, latitude: float, longitude: float, accuracy: float | None = None) -> dict:
	"""
	Check out of an In Progress visit and mark it Completed.

	The check-in location stays on the visit; the check-out position is only
	recorded in the timeline. Visit.on_update stamps Retailer.last_visit.

	:return: ``{"status", "time_on_site_min", "distance_m", "within_geofence", "warning"}``
	"""
	latitude, longitude, accuracy = _parse_position(latitude, longitude, accuracy)

	doc = frappe.get_doc("Visit", visit, for_update=True)
	_check_visit_access(doc)
	if doc.status != "In Progress":
		frappe.throw(_("Only an In Progress visit can be checked out of. This visit is {0}.").format(_(doc.status)))

	doc.check_out_time = now_datetime()
	doc.status = "Completed"
	doc.flags.via_check_in_api = True
	doc.save()

	distance_m = within_geofence = None
	if has_coords(doc.retailer_latitude, doc.retailer_longitude):
		distance_m = round(
			haversine_m(latitude, longitude, flt(doc.retailer_latitude), flt(doc.retailer_longitude)), 2
		)
		within_geofence = int(distance_m <= get_geofence_radius(doc.retailer))

	doc.add_comment(
		"Info",
		_("Checked out at {0}, {1} (±{2} m){3}").format(
			latitude,
			longitude,
			round(accuracy),
			_(", {0} m from the retailer").format(round(distance_m)) if distance_m is not None else "",
		),
	)

	warning = None
	if within_geofence == 0:
		warning = _("You are {0} m away from the retailer").format(round(distance_m))

	return {
		"status": doc.status,
		"time_on_site_min": doc.time_on_site_min,
		"distance_m": distance_m,
		"within_geofence": within_geofence,
		"warning": warning,
	}


def _parse_position(latitude, longitude, accuracy) -> tuple[float, float, float]:
	"""Validate a browser GPS fix: it comes from the client, so check it is a real point."""
	if latitude in (None, "") or longitude in (None, ""):
		frappe.throw(_("Could not read your location. Allow location access and try again."))

	latitude, longitude = flt(latitude), flt(longitude)
	if not has_coords(latitude, longitude) or not is_valid_coords(latitude, longitude):
		frappe.throw(_("Invalid location: {0}, {1}").format(latitude, longitude))

	return latitude, longitude, max(flt(accuracy), 0)


def _check_visit_access(doc) -> None:
	"""Only the visit's own sales rep, or a manager, may check in / out."""
	if frappe.session.user == frappe.db.get_value("Sales Rep", doc.sales_rep, "user"):
		return
	if VISIT_MANAGER_ROLES & set(frappe.get_roles()):
		return
	frappe.throw(
		_("Only the visit's sales rep or a Sales Manager can check in or out."),
		frappe.PermissionError,
	)
