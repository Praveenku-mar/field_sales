# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.connectors import ConnectorError
from field_sales.sync.stock import fetch_live_stock, live_stock_enabled

LOCATION = "100"
GET_STOCK = "field_sales.connectors.shopify.ShopifyConnector.get_stock"


class TestLiveStock(IntegrationTestCase):
	def setUp(self):
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": "test-shop.myshopify.com",
				"shopify_api_version": "2026-10",
				"default_location_id": LOCATION,
			},
		)
		self.tracked = self.make_product("1", inventory_id="5001", qty=50, track=1)
		self.untracked = self.make_product("2", inventory_id="5002", qty=0, track=0)

	def tearDown(self):
		# IntegrationTestCase only rolls back once per class.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def make_product(self, variant_id, inventory_id, qty, track):
		return frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": f"Live stock product {variant_id}",
				"external_product_id": "900",
				"external_variant_id": variant_id,
				"external_inventory_id": inventory_id,
				"track_inventory": track,
				"available_qty": qty,
				"status": "Active",
				"price": 10,
			}
		).insert(ignore_permissions=True)

	def order(self, qty):
		"""An unsaved Field Order with one row per product; only the stock logic is exercised."""
		doc = frappe.new_doc("Field Order")
		for product in (self.tracked, self.untracked):
			doc.append(
				"items",
				{"product": product.name, "qty": qty, "rate": 10, "available_qty": product.available_qty},
			)
		return doc

	# -- fetch_live_stock ----------------------------------------------------

	def test_fetch_asks_store_for_tracked_products_only_and_saves(self):
		with patch(GET_STOCK, return_value={"5001": 3}) as get_stock:
			live = fetch_live_stock([self.tracked.name, self.untracked.name])

		get_stock.assert_called_once_with(["5001"], LOCATION)
		self.assertEqual(live, {self.tracked.name: 3})
		saved = frappe.db.get_value("Platform Product", self.tracked.name, ["available_qty", "stock_updated_at"], as_dict=True)
		self.assertEqual(saved.available_qty, 3)
		self.assertIsNotNone(saved.stock_updated_at)

	def test_fetch_makes_no_store_call_without_tracked_products(self):
		with patch(GET_STOCK) as get_stock:
			self.assertEqual(fetch_live_stock([self.untracked.name]), {})
		get_stock.assert_not_called()

	def test_live_stock_enabled_reads_the_checkbox_as_a_number(self):
		self.assertTrue(live_stock_enabled())
		frappe.db.set_single_value("Store Connection", "enabled", 0)
		# get_singles_dict without cast returns "0", which is truthy.
		self.assertFalse(live_stock_enabled())

	# -- Field Order submit check --------------------------------------------

	def test_submit_check_blocks_when_live_stock_is_short(self):
		doc = self.order(qty=10)  # the synced 50 would have allowed it
		with patch(GET_STOCK, return_value={"5001": 4}):
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.before_submit()
		self.assertIn("exceeds available stock", str(ctx.exception))
		self.assertEqual(doc.items[0].available_qty, 4)
		self.assertEqual(doc.items[0].stock_warning, 1)
		# Untracked products never block.
		self.assertEqual(doc.items[1].stock_warning, 0)

	def test_submit_check_passes_when_live_stock_is_enough(self):
		doc = self.order(qty=10)
		with patch(GET_STOCK, return_value={"5001": 12}):
			doc.before_submit()
		self.assertEqual(doc.items[0].available_qty, 12)
		self.assertEqual(doc.items[0].stock_warning, 0)

	def test_submit_check_stops_when_store_unreachable(self):
		doc = self.order(qty=1)
		with patch(GET_STOCK, side_effect=ConnectorError("Could not reach Shopify", code="NETWORK_ERROR")):
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.before_submit()
		self.assertIn("Could not check live stock", str(ctx.exception))

	def test_submit_check_uses_stored_stock_when_connection_disabled(self):
		frappe.db.set_single_value("Store Connection", "enabled", 0)
		doc = self.order(qty=10)
		with patch(GET_STOCK) as get_stock:
			doc.before_submit()  # synced 50 is enough
		get_stock.assert_not_called()
		self.assertEqual(doc.items[0].available_qty, 50)
