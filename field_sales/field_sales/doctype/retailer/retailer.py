# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address

from field_sales.permissions import get_own_rep, is_admin
from field_sales.sync.customers import SYNCED_FIELDS, enqueue_push


class Retailer(Document):
	def before_insert(self):
		# Retailers are only visible through their Sales Rep, so one created
		# without a rep would vanish for its creator: assign it to them.
		if not self.sales_rep and not is_admin():
			self.sales_rep = get_own_rep()

	def validate(self):
		self._validate_phone()
		self._validate_email()
		self._validate_coordinates()

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

		if self.latitude and not (-90 <= self.latitude <= 90):
			frappe.throw(
				_("Latitude must be between -90 and 90. Received: {0}").format(self.latitude)
			)

		if self.longitude and not (-180 <= self.longitude <= 180):
			frappe.throw(
				_("Longitude must be between -180 and 180. Received: {0}").format(self.longitude)
			)
