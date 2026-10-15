# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from field_sales.api import check_in, check_out

# A point in Coimbatore; the retailer has no location yet, so the first check-in sets it.
LAT, LNG = 11.0168, 76.9558


class TestVisitCheckIn(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.manager_user = self.make_user("checkin.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.rep_user = self.make_user("checkin.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep.name)
		self.sysman = self.make_user("checkin.sysman@example.com", ["System Manager"])

		retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Check-in Test Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Coimbatore",
				"sales_rep": self.rep.name,
			}
		).insert(ignore_permissions=True)
		self.visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.rep.name,
				"retailer": retailer.name,
				"visit_date": nowdate(),
				"status": "Planned",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		# IntegrationTestCase only rolls back once per class.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def make_user(self, email, roles):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user.name

	def make_rep(self, user, reports_to=None):
		return frappe.get_doc({"doctype": "Sales Rep", "user": user, "reports_to": reports_to}).insert(
			ignore_permissions=True
		)

	def check_in_as(self, user):
		frappe.set_user(user)
		try:
			return check_in(self.visit.name, LAT, LNG, 10)
		finally:
			frappe.set_user("Administrator")

	def status(self):
		return frappe.db.get_value("Visit", self.visit.name, "status")

	def test_assigned_rep_can_check_in(self):
		self.check_in_as(self.rep_user)
		self.assertEqual(self.status(), "In Progress")

	def test_rep_manager_cannot_check_in(self):
		with self.assertRaises(frappe.PermissionError) as ctx:
			self.check_in_as(self.manager_user)
		self.assertIn("the rep assigned to this visit", str(ctx.exception))
		self.assertEqual(self.status(), "Planned")

	def test_system_manager_cannot_check_in(self):
		with self.assertRaises(frappe.PermissionError):
			self.check_in_as(self.sysman)
		self.assertEqual(self.status(), "Planned")

	def test_manager_can_still_check_out_a_reps_visit(self):
		self.check_in_as(self.rep_user)
		frappe.set_user(self.manager_user)
		try:
			check_out(self.visit.name, LAT, LNG, 10, no_order_reason="Owner Absent")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self.status(), "Completed")
