# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from field_sales.permissions import clear_allowed_reps_cache


class SalesRep(Document):
	def on_update(self):
		# The hierarchy may have changed; later checks in this request must see it.
		clear_allowed_reps_cache()

	def on_trash(self):
		clear_allowed_reps_cache()
