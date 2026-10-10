# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime

from field_sales.utils.geo import DEFAULT_GEOFENCE_RADIUS_M, has_coords, haversine_m

# Written only by field_sales.api.check_in / check_out, never taken from the client.
CHECK_IN_FIELDS = ("check_in_time", "check_out_time", "latitude", "longitude", "location_accuracy_m")
CHECK_IN_STATUSES = ("In Progress", "Completed")


class Visit(Document):
	def validate(self):
		self._guard_check_in_fields()
		self._validate_checkout_after_checkin()
		self._validate_completed_requires_checkin()
		self._compute_time_on_site()
		self._compute_distance_and_geofence()

	def on_update(self):
		self._update_retailer_last_visit()

	def _guard_check_in_fields(self):
		"""
		Check-in time, location and the In Progress / Completed statuses are set
		by the check-in API from server time. Block them being edited through
		the form or REST, except by a System Manager (corrections, data import).
		"""
		if self.flags.via_check_in_api or "System Manager" in frappe.get_roles():
			return

		before = self.get_doc_before_save()
		for fieldname in CHECK_IN_FIELDS:
			if self._normalized(fieldname, self) != self._normalized(fieldname, before):
				frappe.throw(
					_("{0} is set by Check In / Check Out and cannot be edited.").format(
						_(self.meta.get_label(fieldname))
					)
				)

		old_status = before.status if before else None
		if old_status == self.status:
			return
		# Cancelling a visit that is under way is allowed; nothing else touches these statuses.
		if old_status == "In Progress" and self.status == "Cancelled":
			return
		if old_status in CHECK_IN_STATUSES or self.status in CHECK_IN_STATUSES:
			frappe.throw(_("Use the Check In / Check Out buttons to change the visit status."))

	def _normalized(self, fieldname, doc):
		"""Field value in a comparable form: the client sends strings, the DB returns typed values."""
		value = doc.get(fieldname) if doc else None
		if fieldname in ("check_in_time", "check_out_time"):
			return get_datetime(value) if value else None
		return flt(value)

	def _validate_checkout_after_checkin(self):
		"""Check-out time, when present, must be strictly after check-in time."""
		if self.check_in_time and self.check_out_time:
			if get_datetime(self.check_out_time) <= get_datetime(self.check_in_time):
				frappe.throw(
					_("Check-out time ({0}) must be after check-in time ({1}).").format(
						self.check_out_time, self.check_in_time
					)
				)

	def _validate_completed_requires_checkin(self):
		"""A visit cannot be marked Completed without a check-in time."""
		if self.status == "Completed" and not self.check_in_time:
			frappe.throw(_("Status cannot be set to 'Completed' without a Check-in Time."))

	def _compute_time_on_site(self):
		"""
		Derive time_on_site_min from check-in / check-out timestamps.
		A started minute counts as a full one, so a short visit never shows 0.
		"""
		if self.check_in_time and self.check_out_time:
			delta = get_datetime(self.check_out_time) - get_datetime(self.check_in_time)
			self.time_on_site_min = math.ceil(delta.total_seconds() / 60)
		else:
			self.time_on_site_min = 0

	def _compute_distance_and_geofence(self):
		"""
		Straight-line distance (metres) between the check-in point and the
		retailer's registered coordinates, and whether that is inside the
		retailer's geofence. Skips if either location is missing.
		"""
		if not has_coords(self.latitude, self.longitude):
			return
		if not has_coords(self.retailer_latitude, self.retailer_longitude):
			return

		dist_m = haversine_m(
			flt(self.latitude),
			flt(self.longitude),
			flt(self.retailer_latitude),
			flt(self.retailer_longitude),
		)
		self.distance_from_retailer_m = round(dist_m, 2)
		self.within_geofence = 1 if dist_m <= get_geofence_radius(self.retailer) else 0

	def _update_retailer_last_visit(self):
		"""
		When a visit is saved as Completed, stamp the linked Retailer's
		last_visit field with the check-in timestamp.
		Uses db.set_value to avoid triggering the Retailer's own hooks.
		"""
		if self.status == "Completed" and self.check_in_time and self.retailer:
			frappe.db.set_value(
				"Retailer",
				self.retailer,
				"last_visit",
				self.check_in_time,
				update_modified=False,
			)


def get_geofence_radius(retailer: str) -> int:
	return frappe.db.get_value("Retailer", retailer, "geofence_radius_m") or DEFAULT_GEOFENCE_RADIUS_M
