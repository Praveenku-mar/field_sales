# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from field_sales.connectors.shopify import _draft_line_item, _order_line_item

SHOP = "test-shop.myshopify.com"
PRICE = 100


class TestOrderPricing(IntegrationTestCase):
	"""The rate sent to the store as the line price comes from the product, except for managers."""

	def setUp(self):
		frappe.set_user("Administrator")
		# set_single_value does not validate; everything rolls back after each test.
		frappe.db.set_single_value("Store Connection", {"platform": "Shopify", "enabled": 1, "store_url": SHOP})
		# The site's live sell-from rows would need a default; these tests assume no locations.
		frappe.db.delete("Store Location", {"parenttype": "Store Connection"})
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Pricing product",
				"external_product_id": "9200",
				"external_variant_id": "92",
				"status": "Active",
				"price": PRICE,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.manager_user = self.make_user("pricing.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.rep_user = self.make_user("pricing.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep.name)
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Pricing Stores",
				"phone": "9876543210",
				"address_line1": "5 Market Street",
				"city": "Salem",
				"sales_rep": self.rep.name,
			}
		).insert(ignore_permissions=True)
		self.order = self.make_order()

	def tearDown(self):
		frappe.set_user("Administrator")
		# IntegrationTestCase only rolls back once per class.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

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

	def make_order(self):
		"""A draft of 2 x PRICE for the rep, saved by the rep."""
		frappe.set_user(self.rep_user)
		try:
			return frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": self.retailer.name,
					"sales_rep": self.rep.name,
					"order_date": nowdate(),
					"items": [{"product": self.product.name, "qty": 2, "rate": PRICE}],
				}
			).insert()
		finally:
			frappe.set_user("Administrator")

	def client_save(self, user, **changes):
		"""Save the order through the REST client method, as `user`, with `changes` on the order or `rate` on its row."""
		doc = frappe.get_doc("Field Order", self.order.name).as_dict()
		rate = changes.pop("rate", None)
		if rate is not None:
			doc["items"][0]["rate"] = rate
		doc.update(changes)
		frappe.set_user(user)
		try:
			return frappe.client.save(doc)
		finally:
			frappe.set_user("Administrator")

	def saved(self):
		return frappe.get_doc("Field Order", self.order.name)

	# -- rate --------------------------------------------------------------

	def test_rep_cannot_change_rate(self):
		self.client_save(self.rep_user, rate=1)
		order = self.saved()
		self.assertEqual(flt(order.items[0].rate), PRICE)
		self.assertEqual(flt(order.net_total), 2 * PRICE)

	def test_rep_save_keeps_unchanged_rate_after_store_price_change(self):
		frappe.db.set_value("Platform Product", self.product.name, "price", 120)
		self.client_save(self.rep_user, discount_percent=0)
		self.assertEqual(flt(self.saved().items[0].rate), PRICE)

	def test_rep_changing_rate_gets_current_product_price(self):
		# What "Apply new price" does for a rep.
		frappe.db.set_value("Platform Product", self.product.name, "price", 120)
		self.client_save(self.rep_user, rate=120)
		self.assertEqual(flt(self.saved().items[0].rate), 120)
		frappe.db.set_value("Platform Product", self.product.name, "price", 130)
		self.client_save(self.rep_user, rate=1)
		self.assertEqual(flt(self.saved().items[0].rate), 130)

	def test_rep_save_keeps_managers_rate(self):
		self.client_save(self.manager_user, rate=80)
		doc = frappe.get_doc("Field Order", self.order.name).as_dict()
		doc["items"][0]["qty"] = 3
		frappe.set_user(self.rep_user)
		try:
			frappe.client.save(doc)
		finally:
			frappe.set_user("Administrator")
		order = self.saved()
		self.assertEqual((flt(order.items[0].rate), flt(order.net_total)), (80, 240))

	def test_rep_cannot_change_managers_rate(self):
		self.client_save(self.manager_user, rate=80)
		self.client_save(self.rep_user, rate=1)
		self.assertEqual(flt(self.saved().items[0].rate), PRICE)

	def test_rep_new_row_gets_product_price(self):
		other = frappe.get_doc(
			{**self.product.as_dict(), "name": None, "external_variant_id": "93", "price": 50}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc("Field Order", self.order.name).as_dict()
		doc["items"].append({"doctype": "Field Order Item", "product": other.name, "qty": 1, "rate": 1})
		frappe.set_user(self.rep_user)
		try:
			frappe.client.save(doc)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(flt(self.saved().items[1].rate), 50)

	def test_manager_may_change_rate(self):
		self.client_save(self.manager_user, rate=80)
		order = self.saved()
		self.assertEqual(flt(order.items[0].rate), 80)
		self.assertEqual(flt(order.net_total), 160)

	def test_negative_rate_is_refused_for_manager(self):
		with self.assertRaises(frappe.ValidationError):
			self.client_save(self.manager_user, rate=-1)

	# -- tax and discount --------------------------------------------------

	def test_tax_percent_out_of_range_is_refused(self):
		for value in (150, -5):
			with self.subTest(tax_percent=value), self.assertRaises(frappe.ValidationError):
				self.client_save(self.manager_user, tax_percent=value)

	def test_discount_bounds(self):
		# discount_amount is computed from discount_percent, so check the bounds on a computed doc.
		doc = self.saved()
		doc.discount_amount = -5
		with self.assertRaisesRegex(frappe.ValidationError, "cannot be negative"):
			doc._validate_amounts()
		doc.discount_amount = flt(doc.net_total) + flt(doc.tax_amount) + 1
		with self.assertRaisesRegex(frappe.ValidationError, "more than the order total"):
			doc._validate_amounts()

	def test_discount_amount_sent_by_client_is_recomputed(self):
		self.client_save(self.manager_user, discount_amount=-5)
		self.assertEqual(flt(self.saved().discount_amount), 0)

	# -- normal order ------------------------------------------------------

	def test_normal_order_submits_with_same_totals_and_store_price(self):
		order = self.saved()
		before = (flt(order.net_total), flt(order.tax_amount), flt(order.grand_total))
		frappe.set_user(self.rep_user)
		try:
			order.submit()
		finally:
			frappe.set_user("Administrator")

		order = self.saved()
		self.assertEqual(order.docstatus, 1)
		self.assertEqual((flt(order.net_total), flt(order.tax_amount), flt(order.grand_total)), before)
		row = order.items[0]
		row.external_variant_id = "92"
		self.assertEqual(flt(_draft_line_item(row, "INR")["priceOverride"]["amount"]), PRICE)
		self.assertEqual(flt(_order_line_item(row, "INR")["priceSet"]["shopMoney"]["amount"]), PRICE)

	# -- stock -------------------------------------------------------------

	def stock_order(self, qty):
		"""A rep's draft for `qty` of a tracked product with 5 in stock; live stock is off, so stored stock decides."""
		frappe.db.set_single_value("Store Connection", "default_location_id", "")
		tracked = frappe.get_doc(
			{**self.product.as_dict(), "name": None, "external_variant_id": "94", "track_inventory": 1, "available_qty": 5}
		).insert(ignore_permissions=True)
		frappe.set_user(self.rep_user)
		try:
			order = frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": self.retailer.name,
					"sales_rep": self.rep.name,
					"order_date": nowdate(),
					"items": [{"product": tracked.name, "qty": qty, "rate": PRICE}],
				}
			).insert()
		finally:
			frappe.set_user("Administrator")
		return frappe.get_doc("Field Order", order.name).as_dict()

	def as_rep(self, fn, doc):
		frappe.set_user(self.rep_user)
		try:
			return fn(doc)
		finally:
			frappe.set_user("Administrator")

	def test_rep_cannot_raise_available_qty_on_save(self):
		doc = self.stock_order(10)
		doc["items"][0]["available_qty"] = 9999
		self.as_rep(frappe.client.save, doc)
		row = frappe.get_doc("Field Order", doc["name"]).items[0]
		self.assertEqual((flt(row.available_qty), row.stock_warning), (5, 1))

	def test_rep_cannot_submit_over_stock_with_raised_available_qty(self):
		doc = self.stock_order(10)
		doc["items"][0]["available_qty"] = 9999
		doc["items"][0]["stock_warning"] = 0
		with self.assertRaisesRegex(frappe.ValidationError, "exceeds available stock"):
			self.as_rep(frappe.client.submit, doc)
		self.assertEqual(frappe.db.get_value("Field Order", doc["name"], "docstatus"), 0)

	def test_order_within_stock_submits(self):
		doc = self.stock_order(3)
		self.as_rep(frappe.client.submit, doc)
		self.assertEqual(frappe.db.get_value("Field Order", doc["name"], "docstatus"), 1)
