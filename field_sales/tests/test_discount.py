# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from field_sales.patches.v1_0.discount_percent import execute as run_patch


class TestDiscount(IntegrationTestCase):
	def setUp(self):
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Discount test product",
				"external_product_id": "910",
				"external_variant_id": "911",
				"status": "Active",
				"price": 100,
			}
		).insert(ignore_permissions=True)
		self.rep_user = self.make_user("discount.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, 5)
		self.manager = self.make_user("discount.manager@example.com", ["Sales Manager"])
		self.sysman = self.make_user("discount.sysman@example.com", ["System Manager"])

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

	def make_rep(self, user, max_discount_pct):
		return frappe.get_doc(
			{"doctype": "Sales Rep", "user": user, "max_discount_pct": max_discount_pct}
		).insert(ignore_permissions=True)

	def order(self, percent, rep=None):
		"""An unsaved Field Order of 1000 net (10 x 100)."""
		doc = frappe.new_doc("Field Order")
		doc.sales_rep = (rep or self.rep).name
		doc.discount_percent = percent
		doc.append("items", {"product": self.product.name, "qty": 10, "rate": 100})
		return doc

	def validate_as(self, user, doc):
		frappe.set_user(user)
		try:
			doc.run_method("validate")
		finally:
			frappe.set_user("Administrator")

	def test_amount_is_computed_from_percent(self):
		doc = self.order(2.5)
		self.validate_as(self.rep_user, doc)
		self.assertEqual(flt(doc.net_total), 1000)
		self.assertEqual(flt(doc.discount_amount), 25)
		self.assertEqual(flt(doc.grand_total), 1000 + 50 - 25)  # 5% GST

	def test_rep_within_limit_is_allowed(self):
		self.validate_as(self.rep_user, self.order(5))

	def test_rep_over_limit_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.validate_as(self.rep_user, self.order(5.5))
		self.assertIn("Max discount", str(ctx.exception))

	def test_rep_without_limit_cannot_discount(self):
		other = self.make_rep(self.make_user("discount.nolimit@example.com", ["Field Sales Rep"]), None)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.validate_as(other.user, self.order(1, rep=other))
		self.assertIn("cannot give a discount", str(ctx.exception))
		# No discount is always fine.
		doc = self.order(0, rep=other)
		self.validate_as(other.user, doc)
		self.assertEqual(flt(doc.discount_amount), 0)

	def test_sales_manager_may_exceed_the_limit(self):
		doc = self.order(40)
		self.validate_as(self.manager, doc)
		self.assertEqual(flt(doc.discount_amount), 400)

	def test_system_manager_may_exceed_the_limit(self):
		self.validate_as(self.sysman, self.order(40))

	def test_percent_outside_range_is_refused(self):
		for percent in (-1, 101):
			with self.assertRaises(frappe.ValidationError):
				self.validate_as(self.sysman, self.order(percent))

	def test_percent_outside_range_on_sales_rep_is_refused(self):
		rep = self.make_rep(self.make_user("discount.bad@example.com", ["Field Sales Rep"]), 10)
		rep.max_discount_pct = 150
		with self.assertRaises(frappe.ValidationError):
			rep.save(ignore_permissions=True)

	def test_patch_converts_amount_to_percent_and_is_repeatable(self):
		retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Discount test retailer",
				"phone": "9999999999",
				"address_line1": "1 Test Street",
				"city": "Coimbatore",
				"sales_rep": self.rep.name,
			}
		).insert(ignore_permissions=True)
		order = self.order(0)
		order.retailer = retailer.name
		order.insert(ignore_permissions=True)
		frappe.db.set_value(
			"Field Order", order.name, {"discount_amount": 125, "discount_percent": 0}, update_modified=False
		)

		run_patch()
		values = frappe.db.get_value("Field Order", order.name, ["discount_percent", "discount_amount"], as_dict=True)
		self.assertEqual(flt(values.discount_percent), 12.5)
		self.assertEqual(flt(values.discount_amount), 125)

		run_patch()
		self.assertEqual(flt(frappe.db.get_value("Field Order", order.name, "discount_percent")), 12.5)
