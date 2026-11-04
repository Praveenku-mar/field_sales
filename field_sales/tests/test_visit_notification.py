# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate


class TestVisitNotification(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.manager_user = self.make_user("vnotify.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.rep_user = self.make_user("vnotify.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep.name)
		self.other_user = self.make_user("vnotify.other@example.com", ["Field Sales Rep"])
		self.other_rep = self.make_rep(self.other_user, reports_to=self.manager_rep.name)

		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Notify Test Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Coimbatore",
				"sales_rep": self.rep.name,
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

	def make_visit(self, rep, as_user):
		frappe.set_user(as_user)
		try:
			return frappe.get_doc(
				{
					"doctype": "Visit",
					"sales_rep": rep.name,
					"retailer": self.retailer.name,
					"visit_date": nowdate(),
					"status": "Planned",
				}
			).insert(ignore_permissions=True)
		finally:
			frappe.set_user("Administrator")

	def logs(self, visit, user):
		return frappe.get_all(
			"Notification Log",
			filters={"document_type": "Visit", "document_name": visit.name, "for_user": user},
			pluck="name",
		)

	def test_manager_creating_visit_notifies_rep(self):
		visit = self.make_visit(self.rep, self.manager_user)
		self.assertEqual(len(self.logs(visit, self.rep_user)), 1)

	def test_rep_creating_own_visit_is_not_notified(self):
		visit = self.make_visit(self.rep, self.rep_user)
		self.assertEqual(self.logs(visit, self.rep_user), [])

	def test_resaving_does_not_notify_again(self):
		visit = self.make_visit(self.rep, self.manager_user)
		visit.reload()
		visit.visit_purpose = "Follow-up"
		visit.save(ignore_permissions=True)
		self.assertEqual(len(self.logs(visit, self.rep_user)), 1)

	def test_reassigning_notifies_new_rep_only(self):
		visit = self.make_visit(self.rep, self.manager_user)
		frappe.set_user(self.manager_user)
		try:
			visit.reload()
			visit.sales_rep = self.other_rep.name
			visit.save(ignore_permissions=True)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(len(self.logs(visit, self.other_user)), 1)
		self.assertEqual(len(self.logs(visit, self.rep_user)), 1)

	def test_manager_rescheduling_visit_notifies_rep(self):
		visit = self.make_visit(self.rep, self.manager_user)
		frappe.set_user(self.manager_user)
		try:
			visit.reload()
			visit.visit_date = add_days(visit.visit_date, 1)
			visit.save(ignore_permissions=True)
		finally:
			frappe.set_user("Administrator")
		logs = self.logs(visit, self.rep_user)
		self.assertEqual(len(logs), 2)
		subjects = [frappe.db.get_value("Notification Log", name, "subject") for name in logs]
		self.assertTrue(any("rescheduled" in s.lower() for s in subjects))

	def test_rep_rescheduling_own_visit_is_not_notified(self):
		visit = self.make_visit(self.rep, self.manager_user)
		frappe.set_user(self.rep_user)
		try:
			visit.reload()
			visit.visit_date = add_days(visit.visit_date, 1)
			visit.save(ignore_permissions=True)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(len(self.logs(visit, self.rep_user)), 1)
