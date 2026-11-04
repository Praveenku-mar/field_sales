# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.email.doctype.notification.notification import get_context
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from field_sales.connectors import ConnectorError
from field_sales.sync.customers import push_retailer
from field_sales.sync.notify import clear_role_users_cache
from field_sales.sync.orders import push_order


class TestSyncFailedNotification(IntegrationTestCase):
	"""The "Field Order Sync Failed" email must show rep- and store-supplied text as text."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"platform": "Shopify", "enabled": 1})

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def make_user(self, email, roles):
		if frappe.db.exists("User", email):
			user = frappe.get_doc("User", email)
			user.enabled = 1
			user.save(ignore_permissions=True)
			user.add_roles(*roles)
			return user.name
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user.name

	def make_rep(self, user, reports_to=None):
		if frappe.db.exists("Sales Rep", user):
			rep = frappe.get_doc("Sales Rep", user)
			rep.reports_to = reports_to
			rep.save(ignore_permissions=True)
			return rep
		return frappe.get_doc(
			{"doctype": "Sales Rep", "user": user, "reports_to": reports_to}
		).insert(ignore_permissions=True)

	def make_retailer(self, email="test.retailer@example.com"):
		return frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Test Retailer",
				"phone": "9876543210",
				"address_line1": "10 Test Road",
				"city": "Coimbatore",
				"email": email,
			}
		).insert(ignore_permissions=True)

	def make_product(self):
		if frappe.db.exists("Platform Product", "Shopify-9999"):
			return frappe.get_doc("Platform Product", "Shopify-9999")
		return frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Test Sync Product",
				"external_product_id": "9999",
				"external_variant_id": "9999",
				"status": "Active",
				"price": 100,
			}
		).insert(ignore_permissions=True)

	def render(self, **values):
		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"name": "FO-TEST-00001",
				"retailer": "RET-TEST",
				"retailer_name": "Shop",
				"sales_rep": "rep@example.com",
				"sync_attempts": 5,
				"sync_error": "Store refused the order",
				**values,
			}
		)
		notification = frappe.get_doc("Notification", "Field Order Sync Failed")
		# The same call Notification.send_an_email makes for the body.
		return frappe.render_template(notification.message, get_context(doc), restrict_globals=True)

	def test_retailer_name_html_is_escaped(self):
		html = self.render(retailer_name='<b>Bold</b> <a href="https://evil.example">Pay here</a>')
		self.assertIn("&lt;b&gt;Bold&lt;/b&gt;", html)
		self.assertNotIn('<a href="https://evil.example">', html)

	def test_sync_error_and_rep_are_escaped(self):
		html = self.render(sync_error="<img src=x onerror=alert(1)>", sales_rep="<i>rep</i>")
		self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
		self.assertIn("&lt;i&gt;rep&lt;/i&gt;", html)
		self.assertNotIn("<img", html)

	def test_retailer_id_is_used_when_name_is_empty(self):
		html = self.render(retailer_name=None, retailer="RET-<b>9</b>")
		self.assertIn("RET-&lt;b&gt;9&lt;/b&gt;", html)

	def test_order_link_still_works(self):
		html = self.render()
		self.assertIn(f'href="{frappe.utils.get_url_to_form("Field Order", "FO-TEST-00001")}"', html)
		self.assertIn(">FO-TEST-00001</a>", html)

	def test_order_sync_failure_notifies_responsible_sales_manager(self):
		clear_role_users_cache()
		mgr1_user = self.make_user("syncfail.mgr1@example.com", ["Field Sales Rep", "Sales Manager"])
		mgr1_rep = self.make_rep(mgr1_user)
		mgr2_user = self.make_user("syncfail.mgr2@example.com", ["Field Sales Rep", "Sales Manager"])
		mgr2_rep = self.make_rep(mgr2_user)

		rep1_user = self.make_user("syncfail.rep1@example.com", ["Field Sales Rep"])
		rep1 = self.make_rep(rep1_user, reports_to=mgr1_rep.name)

		retailer = self.make_retailer("syncfail.ret1@example.com")
		product = self.make_product()

		order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": retailer.name,
				"sales_rep": rep1.name,
				"order_date": nowdate(),
				"items": [{"product": product.name, "qty": 1, "rate": 100}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Field Order", order.name, "docstatus", 1)

		with patch(
			"field_sales.connectors.shopify.ShopifyConnector.create_order",
			side_effect=ConnectorError("Store refused order"),
		):
			push_order(order.name)

		# Check notification logs for Manager 1
		mgr1_logs = frappe.get_all(
			"Notification Log",
			filters={"document_type": "Field Order", "document_name": order.name, "for_user": mgr1_user},
			pluck="subject",
		)
		self.assertEqual(len(mgr1_logs), 1)
		self.assertIn("failed to sync", mgr1_logs[0])

		# Manager 2 and Rep 1 should not receive it
		mgr2_logs = frappe.get_all(
			"Notification Log",
			filters={"document_type": "Field Order", "document_name": order.name, "for_user": mgr2_user},
		)
		self.assertEqual(len(mgr2_logs), 0)

		rep1_logs = frappe.get_all(
			"Notification Log",
			filters={"document_type": "Field Order", "document_name": order.name, "for_user": rep1_user},
		)
		self.assertEqual(len(rep1_logs), 0)

	def test_customer_sync_failure_notifies_system_administrator(self):
		clear_role_users_cache()
		sysman_user = self.make_user("syncfail.sysman@example.com", ["System Manager"])
		salesman_user = self.make_user("syncfail.salesman@example.com", ["Sales Manager"])
		rep_user = self.make_user("syncfail.rep@example.com", ["Field Sales Rep"])

		retailer = self.make_retailer("syncfail.ret2@example.com")

		with patch(
			"field_sales.connectors.shopify.ShopifyConnector.create_customer",
			side_effect=ConnectorError("Customer email invalid"),
		):
			push_retailer(retailer.name)

		# System Manager and Administrator should receive notification
		sysman_logs = frappe.get_all(
			"Notification Log",
			filters={"document_type": "Retailer", "document_name": retailer.name, "for_user": sysman_user},
			pluck="subject",
		)
		self.assertEqual(len(sysman_logs), 1)
		self.assertIn("failed to sync", sysman_logs[0])

		admin_logs = frappe.get_all(
			"Notification Log",
			filters={"document_type": "Retailer", "document_name": retailer.name, "for_user": "Administrator"},
			pluck="subject",
		)
		self.assertEqual(len(admin_logs), 1)

		# Sales Manager and Rep should NOT receive notification
		salesman_logs = frappe.get_all(
			"Notification Log",
			filters={"document_type": "Retailer", "document_name": retailer.name, "for_user": salesman_user},
		)
		self.assertEqual(len(salesman_logs), 0)

		rep_logs = frappe.get_all(
			"Notification Log",
			filters={"document_type": "Retailer", "document_name": retailer.name, "for_user": rep_user},
		)
		self.assertEqual(len(rep_logs), 0)
