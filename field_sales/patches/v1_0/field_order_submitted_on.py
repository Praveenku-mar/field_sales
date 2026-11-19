# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Fill ``Field Order.submitted_on`` for orders submitted before the field existed. Their submit
	time was not recorded, so ``modified`` stands in (close for most orders; later server writes
	use ``update_modified=False``). Only the plan's monthly order count reads it. Safe to re-run.
	"""
	if not frappe.db.has_column("Field Order", "submitted_on"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabField Order`
		SET submitted_on = modified
		WHERE docstatus > 0 AND submitted_on IS NULL
		"""
	)
