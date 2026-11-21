# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The order page's Location picker (``api.set_order_location``): a new draft made by ``create_order``
has no items yet, and switching its location must not fail with "Items is mandatory" (it did through
``frappe.client.set_value``), while every other rule still applies.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from field_sales.api import create_order, set_order_location
from field_sales.utils.store import get_store, set_store_value

MAIN = "100"
WAREHOUSE = "200"
CLOSED = "300"  # in the table, but not ticked


class TestOrderLocationSwitch(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_store_value(
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": "test-shop.myshopify.com",
				"shopify_api_version": "2026-10",
				"default_location_id": MAIN,
				"default_location_name": "Main",
			}
		)
		settings = get_store()
		settings.set("locations", [])
		for location_id, name, sell_from in (
			(MAIN, "Main", 1),
			(WAREHOUSE, "Warehouse", 1),
			(CLOSED, "Closed", 0),
		):
			settings.append(
				"locations", {"location_id": location_id, "location_name": name, "sell_from": sell_from}
			)
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)

		self.rep_user = self.user("switch.rep@example.com")
		self.rep = (
			frappe.get_doc({"doctype": "Sales Rep", "user": self.rep_user})
			.insert(ignore_permissions=True)
			.name
		)
		self.retailer = (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": "Switch Stores",
					"phone": "9876543210",
					"address_line1": "1 Main Road",
					"city": "Coimbatore",
					"sales_rep": self.rep,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def user(self, email):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		return user.name

	def new_draft(self):
		"""A draft as the order page starts it: header only, no items."""
		frappe.set_user(self.rep_user)
		try:
			return create_order(self.retailer, self.rep, nowdate())["name"]
		finally:
			frappe.set_user("Administrator")

	def test_a_new_draft_without_items_can_switch_location(self):
		name = self.new_draft()
		self.assertFalse(frappe.get_doc("Field Order", name).items)

		frappe.set_user(self.rep_user)
		result = set_order_location(name, WAREHOUSE)
		frappe.set_user("Administrator")

		self.assertEqual(result, {"location_id": WAREHOUSE, "location_name": "Warehouse"})
		self.assertEqual(frappe.db.get_value("Field Order", name, "location_id"), WAREHOUSE)

	def test_a_location_the_shop_does_not_sell_from_is_refused(self):
		name = self.new_draft()
		with self.assertRaises(frappe.ValidationError):
			set_order_location(name, CLOSED)
		self.assertEqual(frappe.db.get_value("Field Order", name, "location_id"), MAIN)

	def test_a_submitted_order_is_refused(self):
		name = self.new_draft()
		frappe.db.set_value("Field Order", name, "docstatus", 1, update_modified=False)
		with self.assertRaises(frappe.ValidationError):
			set_order_location(name, WAREHOUSE)

	def test_another_teams_rep_cannot_switch_it(self):
		name = self.new_draft()
		outsider = self.user("switch.outsider@example.com")
		frappe.get_doc({"doctype": "Sales Rep", "user": outsider}).insert(ignore_permissions=True)

		frappe.set_user(outsider)
		with self.assertRaises(frappe.PermissionError):
			set_order_location(name, WAREHOUSE)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Field Order", name, "location_id"), MAIN)
