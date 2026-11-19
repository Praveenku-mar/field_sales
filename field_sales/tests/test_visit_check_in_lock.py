# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Review finding F11: once a visit is checked in, its retailer, date and rep are fixed. Before, a rep
could check in at one shop and save the visit with another retailer (or date), and it kept "inside
geofence", measured against the first shop's position.
"""

import frappe
from frappe.client import save
from frappe.utils import add_days, nowdate

from field_sales.api import check_out
from field_sales.tests.test_visit_check_in import LAT, LNG, TestVisitCheckIn


class TestVisitCheckInLock(TestVisitCheckIn):
	def far_retailer(self):
		return frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Far Away Stores",
				"phone": "9876500000",
				"address_line1": "2 Beach Road",
				"city": "Chennai",
				"sales_rep": self.rep.name,
				"latitude": 13.0827,
				"longitude": 80.2707,
			}
		).insert(ignore_permissions=True)

	def save_as(self, user, **changes):
		frappe.set_user(user)
		try:
			doc = frappe.get_doc("Visit", self.visit.name).as_dict()
			doc.update(changes)
			return save(doc)
		finally:
			frappe.set_user("Administrator")

	def test_rep_cannot_move_a_checked_in_visit_to_another_retailer(self):
		far = self.far_retailer()
		self.check_in_as(self.rep_user)

		with self.assertRaises(frappe.PermissionError):
			self.save_as(self.rep_user, retailer=far.name)

		self.assertNotEqual(frappe.db.get_value("Visit", self.visit.name, "retailer"), far.name)

	def test_rep_cannot_move_a_checked_in_visit_to_another_day(self):
		self.check_in_as(self.rep_user)
		with self.assertRaises(frappe.PermissionError):
			self.save_as(self.rep_user, visit_date=add_days(nowdate(), -1))

	def test_manager_cannot_give_a_checked_in_visit_to_another_rep(self):
		self.check_in_as(self.rep_user)
		with self.assertRaises(frappe.PermissionError):
			self.save_as(self.manager_user, sales_rep=self.manager_rep.name)

	def test_notes_can_still_be_saved_after_check_in(self):
		self.check_in_as(self.rep_user)
		self.save_as(self.rep_user, notes="Owner asked for the new catalogue")
		self.assertEqual(frappe.db.get_value("Visit", self.visit.name, "notes"), "Owner asked for the new catalogue")

	def test_a_planned_visit_can_still_be_rescheduled_and_moved(self):
		far = self.far_retailer()
		self.save_as(self.manager_user, visit_date=add_days(nowdate(), 1), retailer=far.name)
		values = frappe.db.get_value("Visit", self.visit.name, ["visit_date", "retailer"], as_dict=True)
		self.assertEqual((str(values.visit_date), values.retailer), (add_days(nowdate(), 1), far.name))

	def test_check_out_and_cancel_still_work(self):
		self.check_in_as(self.rep_user)
		self.save_as(self.rep_user, status="Cancelled")
		self.assertEqual(self.status(), "Cancelled")

	def test_check_out_is_not_blocked(self):
		self.check_in_as(self.rep_user)
		frappe.set_user(self.rep_user)
		try:
			check_out(self.visit.name, LAT, LNG, 10, no_order_reason="Owner Absent")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self.status(), "Completed")

	def test_system_manager_may_correct_it(self):
		far = self.far_retailer()
		self.check_in_as(self.rep_user)
		self.save_as(self.sysman, retailer=far.name)
		self.assertEqual(frappe.db.get_value("Visit", self.visit.name, "retailer"), far.name)

