# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, validate_email_address

from field_sales.permissions import get_own_rep, is_admin
from field_sales.sync.customers import SYNCED_FIELDS, enqueue_push
from field_sales.utils.geo import has_coords
from field_sales.utils.guards import is_system_manager, keep_server_fields

# Written only by the server: the customer sync (_record_result) and Visit (last_visit).
SERVER_FIELDS = (
	"platform",
	"external_customer_id",
	"sync_status",
	"last_synced",
	"last_sync_attempt",
	"sync_attempts",
	"sync_error",
	"last_visit",
)
# May move a located retailer or change its geofence radius (both decide check-in distance).
LOCATION_EDIT_ROLES = {"Sales Manager", "System Manager"}


class Retailer(Document):
	def before_insert(self):
		# Retailers are only visible through their Sales Rep, so one created
		# without a rep would vanish for its creator: assign it to them.
		if not self.sales_rep and not is_admin():
			self.sales_rep = get_own_rep()

	def validate(self):
		self._keep_server_fields()
		self._guard_location()
		self._validate_phone()
		self._validate_email()
		self._validate_coordinates()

	def _keep_server_fields(self):
		"""
		A planted external_customer_id would link this retailer and its orders to another
		store customer, and the next push would overwrite that customer.
		"""
		if self.flags.ignore_sync:  # the customer sync's own save (sync/customers.py _record_result)
			return
		if self.is_new() or not is_system_manager():
			keep_server_fields(self, SERVER_FIELDS)

	def _guard_location(self):
		"""
		A rep could pass any check-in by moving the retailer to where they stand or widening
		its geofence. Reps may set a retailer's first location; after that only managers
		move it, and only managers change the radius (new retailers get the default).
		"""
		if LOCATION_EDIT_ROLES & set(frappe.get_roles()):
			return
		keep_server_fields(self, ("geofence_radius_m",))
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before or not has_coords(before.latitude, before.longitude):
			return  # the first location can still be set
		if flt(self.latitude) != flt(before.latitude) or flt(self.longitude) != flt(before.longitude):
			frappe.throw(
				_("This retailer's location is already set. Ask a Sales Manager to change it."),
				frappe.PermissionError,
			)

	def on_update(self):
		self._push_to_store_if_changed()

	def _push_to_store_if_changed(self):
		"""Queue a push to the store when a field the store receives has changed."""
		# Set by the sync itself when it saves the result, so it does not re-trigger.
		if self.flags.ignore_sync:
			return
		if not frappe.db.get_single_value("Store Connection", "enabled"):
			return
		if any(self.has_value_changed(field) for field in SYNCED_FIELDS):
			enqueue_push(self.name)

	def _validate_phone(self):
		
		if not self.phone:
			return

		cleaned = self.phone.strip()
		# Remove optional +91 country code (with any surrounding spaces)
		cleaned = re.sub(r"^\+91\s*", "", cleaned)
		# Collapse all remaining whitespace / dashes that users might type
		cleaned = re.sub(r"[\s\-]+", "", cleaned)

		if not re.fullmatch(r"\d{10}", cleaned):
			frappe.throw(
				_("Phone number must be 10 digits (optionally prefixed with +91). Received: {0}").format(
					self.phone
				)
			)

	def _validate_email(self):
		"""Email is optional; when given it must be a single, well-formed address."""
		if not self.email:
			return
		self.email = self.email.strip()
		# validate_email_address accepts comma-separated lists; the store takes one address.
		if "," in self.email or ";" in self.email:
			frappe.throw(_("Enter a single email address. Received: {0}").format(self.email))
		validate_email_address(self.email, throw=True)

	def _validate_coordinates(self):

		# flt(): the value may still be a string at validate time (e.g. set_value
		# from a client form), and comparing a str with int raises.
		if self.latitude and not (-90 <= flt(self.latitude) <= 90):
			frappe.throw(
				_("Latitude must be between -90 and 90. Received: {0}").format(self.latitude)
			)

		if self.longitude and not (-180 <= flt(self.longitude) <= 180):
			frappe.throw(
				_("Longitude must be between -180 and 180. Received: {0}").format(self.longitude)
			)
