# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.connectors import get_connector
from field_sales.connectors.base import STOCK_TOPIC
from field_sales.connectors.shopify import _order_note, _order_tags
from field_sales.patches.v1_0.multi_location import execute as run_patch
from field_sales.sync.products import sync_one_product
from field_sales.sync.stock import (
	fetch_live_stock,
	get_location_stock,
	get_sell_locations,
	set_location_stock,
)
from field_sales.sync.webhooks import process_event

MAIN = "100"
WAREHOUSE = "200"
CLOSED = "300"  # in the table, but not ticked
INVENTORY = "5001"
GET_STOCK = "field_sales.connectors.shopify.ShopifyConnector.get_stock"
GET_PRODUCT = "field_sales.connectors.shopify.ShopifyConnector.get_product"


class TestMultiLocation(IntegrationTestCase):
	def setUp(self):
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": "test-shop.myshopify.com",
				"shopify_api_version": "2026-10",
				"default_location_id": MAIN,
				"default_location_name": "Main",
			},
		)
		settings = frappe.get_doc("Store Connection")
		settings.set("locations", [])
		for location_id, name, sell_from in ((MAIN, "Main", 1), (WAREHOUSE, "Warehouse", 1), (CLOSED, "Closed", 0)):
			settings.append("locations", {"location_id": location_id, "location_name": name, "sell_from": sell_from})
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)

		self.product = self.make_product("1", INVENTORY, 50)

	def tearDown(self):
		# IntegrationTestCase only rolls back once per class.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def make_product(self, variant_id, inventory_id, qty):
		return frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": f"Multi location product {variant_id}",
				"external_product_id": "900",
				"external_variant_id": variant_id,
				"external_inventory_id": inventory_id,
				"track_inventory": 1,
				"available_qty": qty,
				"status": "Active",
				"price": 10,
			}
		).insert(ignore_permissions=True)

	def order(self, qty=10, location_id=None):
		"""An unsaved Field Order with one row for the product."""
		doc = frappe.new_doc("Field Order")
		doc.location_id = location_id
		doc.append("items", {"product": self.product.name, "qty": qty, "rate": 10, "available_qty": 50})
		return doc

	def stock_row(self, location_id, product=None):
		return frappe.db.get_value(
			"Product Stock",
			{"product": (product or self.product).name, "location_id": location_id},
			["available_qty", "stock_updated_at", "location_name"],
			as_dict=True,
		)

	def stock_event(self, location, available, updated_at="2026-10-14T10:00:00Z"):
		return {
			"topic": STOCK_TOPIC,
			"platform_topic": "inventory_levels/update",
			"event_id": frappe.generate_hash(length=10),
			"updated_at": updated_at,
			"payload": {
				"inventory_item_id": int(INVENTORY),
				"location_id": int(location),
				"available": available,
				"updated_at": updated_at,
			},
		}

	# -- Store Connection ----------------------------------------------------

	def test_sell_locations_list_default_first_and_skip_unticked(self):
		self.assertEqual(
			get_sell_locations(),
			[
				{"location_id": MAIN, "location_name": "Main", "is_default": True},
				{"location_id": WAREHOUSE, "location_name": "Warehouse", "is_default": False},
			],
		)

	def test_default_location_is_added_and_ticked_on_save(self):
		settings = frappe.get_doc("Store Connection")
		settings.default_location_id = "400"
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)
		self.assertIn("400", [loc["location_id"] for loc in get_sell_locations()])

		settings.reload()
		next(r for r in settings.locations if r.location_id == MAIN).sell_from = 0
		settings.default_location_id = MAIN
		settings.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Store Location", {"location_id": MAIN}, "sell_from"), 1)

	def test_duplicate_location_is_refused(self):
		settings = frappe.get_doc("Store Connection")
		settings.append("locations", {"location_id": WAREHOUSE, "sell_from": 1})
		settings.flags.ignore_mandatory = True
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	# -- set_location_stock / live stock -------------------------------------

	def test_set_location_stock_updates_the_product_only_for_the_default(self):
		set_location_stock(self.product.name, WAREHOUSE, 7, "Warehouse")
		self.assertEqual(self.stock_row(WAREHOUSE).available_qty, 7)
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "available_qty"), 50)

		set_location_stock(self.product.name, MAIN, 9, "Main")
		product = frappe.db.get_value("Platform Product", self.product.name, ["available_qty", "stock_updated_at"], as_dict=True)
		self.assertEqual(product.available_qty, 9)
		self.assertIsNotNone(product.stock_updated_at)

		# A second write updates the row instead of adding one.
		set_location_stock(self.product.name, WAREHOUSE, 5)
		self.assertEqual(self.stock_row(WAREHOUSE).available_qty, 5)
		self.assertEqual(frappe.db.count("Product Stock", {"product": self.product.name, "location_id": WAREHOUSE}), 1)

	def test_fetch_live_stock_at_another_location(self):
		with patch(GET_STOCK, return_value={INVENTORY: 4}) as get_stock:
			live = fetch_live_stock([self.product.name], WAREHOUSE)

		get_stock.assert_called_once_with([INVENTORY], WAREHOUSE)
		self.assertEqual(live, {self.product.name: 4})
		self.assertEqual(self.stock_row(WAREHOUSE).available_qty, 4)
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "available_qty"), 50)

	def test_fetch_live_stock_refuses_a_location_not_sold_from(self):
		with patch(GET_STOCK) as get_stock:
			with self.assertRaises(frappe.ValidationError):
				fetch_live_stock([self.product.name], CLOSED)
		get_stock.assert_not_called()

	# -- Field Order ----------------------------------------------------------

	def test_default_location_applied_when_empty(self):
		doc = self.order()
		doc._validate_location()
		self.assertEqual(doc.location_id, MAIN)
		self.assertEqual(doc.location_name, "Main")

	def test_location_not_sold_from_is_refused(self):
		for location_id in (CLOSED, "999"):
			doc = self.order(location_id=location_id)
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc._validate_location()
			self.assertIn("not one the shop sells from", str(ctx.exception))

	def test_changing_a_drafts_location_takes_stock_from_that_location(self):
		set_location_stock(self.product.name, WAREHOUSE, 2, "Warehouse")
		doc = self.order(qty=10, location_id=WAREHOUSE)

		doc._validate_location()
		doc._set_row_stock()
		doc._compute_item_amounts()

		self.assertEqual(doc.location_name, "Warehouse")
		self.assertEqual(doc.items[0].available_qty, 2)
		self.assertEqual(doc.items[0].stock_warning, 1)

	def test_item_stock_is_not_fetched_from_the_product(self):
		# Platform Product.available_qty is the default location's stock; a fetch_from would
		# overwrite the order location's figure on every save.
		self.assertFalse(frappe.get_meta("Field Order Item").get_field("available_qty").fetch_from)

	def test_refresh_then_save_keeps_the_orders_location_stock(self):
		# The reported bug: Refresh Stock on a Warehouse order showed the default location's 50.
		doc = self.order(qty=10, location_id=WAREHOUSE)
		with patch(GET_STOCK, return_value={INVENTORY: 7}) as get_stock:
			live = fetch_live_stock([self.product.name], WAREHOUSE)
		get_stock.assert_called_once_with([INVENTORY], WAREHOUSE)
		doc.items[0].available_qty = live[self.product.name]

		doc.run_method("validate")  # what doc.save() runs after Frappe's own link fetches

		self.assertEqual(doc.items[0].available_qty, 7)
		self.assertEqual(doc.items[0].stock_warning, 1)

	def test_without_any_location_rows_use_the_products_stock(self):
		frappe.db.set_single_value("Store Connection", {"default_location_id": None, "default_location_name": None})
		frappe.db.delete("Store Location", {"parent": "Store Connection"})
		self.assertEqual(get_location_stock([self.product.name]), {self.product.name: 50})

	def test_submit_check_uses_the_orders_location(self):
		doc = self.order(qty=10, location_id=WAREHOUSE)
		asked = []

		def get_stock(self_, inventory_ids, location_id):
			asked.append(location_id)
			return {INVENTORY: 3}

		with patch(GET_STOCK, get_stock):
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.before_submit()

		self.assertEqual(asked, [WAREHOUSE])
		self.assertIn("exceeds available stock", str(ctx.exception))
		self.assertEqual(self.stock_row(WAREHOUSE).available_qty, 3)
		# The default location's figure is untouched.
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "available_qty"), 50)

	def test_get_location_stock_falls_back_to_the_product_only_at_the_default(self):
		self.assertEqual(get_location_stock([self.product.name]), {self.product.name: 50})
		self.assertEqual(get_location_stock([self.product.name], WAREHOUSE), {self.product.name: 0.0})

	# -- Product sync ----------------------------------------------------------

	def test_product_sync_fills_every_sell_location(self):
		item = {
			"external_product_id": "900",
			"external_variant_id": "1",
			"external_inventory_id": INVENTORY,
			"product_title": "Synced",
			"variant_title": "",
			"sku": "",
			"barcode": "",
			"price": 5.0,
			"compare_at_price": None,
			"collection": "",
			"image_url": "",
			"status": "Active",
			"track_inventory": True,
		}
		untracked = {
			**item,
			"external_variant_id": "2",
			"external_inventory_id": "5002",
			"track_inventory": False,
		}
		by_location = {MAIN: {INVENTORY: 6.0}, WAREHOUSE: {INVENTORY: 11.0}}
		asked = []

		def get_stock(self_, inventory_ids, location_id):
			asked.append(location_id)
			return by_location[location_id]

		with patch(GET_PRODUCT, return_value=[item, untracked]), patch(GET_STOCK, get_stock):
			sync_one_product(get_connector(), "900")

		self.assertEqual(sorted(asked), [MAIN, WAREHOUSE])  # not the unticked location
		self.assertEqual(self.stock_row(MAIN).available_qty, 6)
		self.assertEqual(self.stock_row(WAREHOUSE).available_qty, 11)
		self.assertIsNone(self.stock_row(CLOSED))
		# Platform Product keeps the default location's figure.
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "available_qty"), 6)
		# Untracked products get no stock rows.
		self.assertEqual(frappe.db.count("Product Stock", {"product": "Shopify-2"}), 0)

	# -- Webhooks ----------------------------------------------------------------

	def test_webhook_updates_the_events_location(self):
		result = process_event("Shopify", self.stock_event(WAREHOUSE, 8))

		self.assertEqual(result["updated"], 1)
		self.assertEqual(self.stock_row(WAREHOUSE).available_qty, 8)
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "available_qty"), 50)

		process_event("Shopify", self.stock_event(MAIN, 12))
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "available_qty"), 12)
		self.assertEqual(self.stock_row(MAIN).available_qty, 12)

	def test_webhook_ignores_unticked_location(self):
		result = process_event("Shopify", self.stock_event(CLOSED, 8))
		self.assertEqual(result["note"], "Ignored: location not sold from")
		self.assertIsNone(self.stock_row(CLOSED))

	def test_webhook_order_is_kept_per_location(self):
		process_event("Shopify", self.stock_event(WAREHOUSE, 8, "2026-10-14T10:00:00Z"))

		# Older for the warehouse: dropped.
		result = process_event("Shopify", self.stock_event(WAREHOUSE, 1, "2026-10-14T09:00:00Z"))
		self.assertEqual(result["note"], "Ignored: older than stored stock")
		self.assertEqual(self.stock_row(WAREHOUSE).available_qty, 8)

		# The same time at the default location is not compared with the warehouse.
		result = process_event("Shopify", self.stock_event(MAIN, 3, "2026-10-14T09:00:00Z"))
		self.assertEqual(result["updated"], 1)
		self.assertEqual(self.stock_row(MAIN).available_qty, 3)

	# -- Shopify order tags and note ---------------------------------------------

	def test_order_tag_is_field_sales_and_note_carries_the_location(self):
		order = frappe._dict(name="FO-1", sales_rep="rep@example.com", location_id=WAREHOUSE, location_name="Warehouse, East")
		# The order number and rep are metafields now; the location stays in the note.
		self.assertEqual(_order_tags(order), ["field-sales"])
		self.assertTrue(_order_note(order).endswith(" · Location Warehouse, East"))

		order.location_id = None
		self.assertNotIn("Location", _order_note(order))

	# -- Migration patch ------------------------------------------------------------

	def test_patch_is_idempotent_and_copies_stock(self):
		frappe.db.delete("Store Location")
		frappe.db.delete("Product Stock")
		frappe.db.set_value("Platform Product", self.product.name, "stock_updated_at", "2026-10-01 10:00:00")
		untracked = self.make_product("2", "5002", 0)
		frappe.db.set_value("Platform Product", untracked.name, "track_inventory", 0)

		order = self.order()
		order.flags.ignore_mandatory = True
		order.flags.ignore_links = True
		order.insert(ignore_permissions=True)
		frappe.db.set_value("Field Order", order.name, {"location_id": "", "location_name": ""})

		for _ in range(2):
			run_patch()

		self.assertEqual(
			frappe.get_all("Store Location", filters={"parent": "Store Connection"}, fields=["location_id", "sell_from"]),
			[{"location_id": MAIN, "sell_from": 1}],
		)
		row = self.stock_row(MAIN)
		self.assertEqual(row.available_qty, 50)
		self.assertEqual(str(row.stock_updated_at), "2026-10-01 10:00:00")
		self.assertEqual(frappe.db.count("Product Stock", {"location_id": MAIN}), 1)  # untracked product has none
		self.assertEqual(frappe.db.get_value("Field Order", order.name, "location_id"), MAIN)
		self.assertEqual(frappe.db.get_value("Field Order", order.name, "location_name"), "Main")
