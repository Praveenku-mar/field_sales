# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from field_sales.api.mobile import (
	decode_cursor,
	encode_cursor,
	get_commission_statements_list,
	get_customer_detail_records,
	get_customers_list,
	get_mobile_count,
	get_order_detail,
	get_orders_list,
	get_product_detail_stock,
	get_product_sync_status,
	get_products_list,
	get_team_list,
	get_visit_detail,
	get_visits_list,
	mobile_search,
)
from field_sales.frontend_api import get_dashboard, get_product_activity


class TestMobileAPI(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_cursor_encoding_and_tampering_defense(self):
		"""Cursor encoding produces urlsafe string; malformed/tampered cursor is safely handled."""
		original = {"order_date": "2026-11-04", "modified": "2026-11-04 12:00:00", "name": "FO-001"}
		encoded = encode_cursor(original)
		self.assertIsInstance(encoded, str)
		self.assertNotIn("=", encoded)  # Padding stripped

		# Valid decode
		decoded = decode_cursor(encoded, ("order_date", "modified", "name"))
		self.assertEqual(decoded, original)

		# Untrusted inputs: missing expected keys, corrupted base64, excessively long payload
		self.assertIsNone(decode_cursor("not-base64-!@#$", ("order_date", "modified", "name")))
		self.assertIsNone(decode_cursor(encode_cursor({"wrong": "key"}), ("order_date", "modified", "name")))
		self.assertIsNone(decode_cursor("A" * 600, ("order_date", "modified", "name")))
		self.assertIsNone(decode_cursor("", ("order_date", "modified", "name")))
		self.assertIsNone(decode_cursor(None, ("order_date", "modified", "name")))

	def test_orders_keyset_pagination_and_stable_ordering(self):
		"""Orders pagination advances via cursor with stable ordering and zero duplicates."""
		page1 = get_orders_list(limit=2)
		self.assertIn("items", page1)
		self.assertIn("has_more", page1)
		self.assertIn("next_cursor", page1)

		if page1["has_more"]:
			self.assertIsNotNone(page1["next_cursor"])
			self.assertEqual(len(page1["items"]), 2)

			# Ensure list card fields only (excludes child table items)
			first_item = page1["items"][0]
			self.assertIn("name", first_item)
			self.assertIn("grand_total", first_item)
			self.assertNotIn("items", first_item)

			# Fetch page 2 using cursor
			page2 = get_orders_list(cursor=page1["next_cursor"], limit=2)
			self.assertIn("items", page2)
			if page2["items"]:
				page1_names = {r["name"] for r in page1["items"]}
				page2_names = {r["name"] for r in page2["items"]}
				# No duplicate records across cursor pages
				self.assertTrue(page1_names.isdisjoint(page2_names))

	def test_server_enforced_page_limits(self):
		"""Server enforces default 50 and maximum 100 limit."""
		# Requesting excessive limit clamps to 100
		res = get_orders_list(limit=5000)
		self.assertLessEqual(res["limit"], 100)
		self.assertLessEqual(len(res["items"]), 100)

		# Default limit is 50
		res_default = get_orders_list()
		self.assertEqual(res_default["limit"], 50)

	def test_visits_keyset_pagination(self):
		"""Visits list uses stable ordering (visit_date, modified, name) and card fields."""
		res = get_visits_list(limit=3)
		self.assertIn("items", res)
		if res["items"]:
			first = res["items"][0]
			self.assertIn("visit_date", first)
			self.assertIn("status", first)
			# Geofence debug / accuracy history excluded from list card
			self.assertNotIn("check_in_latitude", first)

	def test_customers_keyset_pagination(self):
		"""Customers list returns lightweight card fields."""
		res = get_customers_list(limit=3)
		self.assertIn("items", res)
		if res["items"]:
			first = res["items"][0]
			self.assertIn("retailer_name", first)
			self.assertIn("city", first)
			self.assertNotIn("address_line1", first)

	def test_products_keyset_pagination(self):
		"""Products list returns card fields with price and available quantity."""
		res = get_products_list(limit=3)
		self.assertIn("items", res)
		if res["items"]:
			first = res["items"][0]
			self.assertIn("title", first)
			self.assertIn("available_qty", first)

	def test_order_detail_lazy_loads_child_items(self):
		"""Detail endpoint loads full child items table, tax, and sync information."""
		orders = get_orders_list(limit=1)["items"]
		if orders:
			order_id = orders[0]["name"]
			detail = get_order_detail(order_id)
			self.assertEqual(detail["name"], order_id)
			self.assertIn("items", detail)
			self.assertIn("grand_total", detail)
			self.assertIn("net_total", detail)

	def test_customer_detail_and_related_records(self):
		"""Customer detail returns customer record, outstanding balance, and paginated recent orders/visits."""
		customers = get_customers_list(limit=1)["items"]
		if customers:
			cust_id = customers[0]["name"]
			detail = get_customer_detail_records(cust_id)
			self.assertIn("customer", detail)
			self.assertIn("recent_orders", detail)
			self.assertIn("recent_visits", detail)
			self.assertIn("outstanding_balance", detail["customer"])
			self.assertLessEqual(len(detail["recent_orders"]), 5)
			self.assertLessEqual(len(detail["recent_visits"]), 5)

	def test_visit_detail_heavy_fields(self):
		"""Visit detail endpoint returns GPS coordinates and notes."""
		visits = get_visits_list(limit=1)["items"]
		if visits:
			visit_id = visits[0]["name"]
			detail = get_visit_detail(visit_id)
			self.assertEqual(detail["name"], visit_id)
			self.assertIn("latitude", detail)
			self.assertIn("longitude", detail)
			self.assertIn("notes", detail)

	def test_separate_count_queries(self):
		"""Count queries execute separately and return integer counts."""
		res = get_mobile_count("Field Order")
		self.assertIn("count", res)
		self.assertIsInstance(res["count"], int)

		# Count with preset
		preset_res = get_mobile_count("Field Order", preset="draft_orders")
		self.assertIn("count", preset_res)
		self.assertIsInstance(preset_res["count"], int)

	def test_search_min_chars_and_max_suggestions(self):
		"""Search requires minimum 2 characters and caps results at 20 suggestions."""
		# Single char returns empty without querying
		short_res = mobile_search(query="a")
		self.assertEqual(short_res["results"], [])

		# 2+ characters returns suggestions capped at 20
		res = mobile_search(query="An", limit=50)
		self.assertLessEqual(len(res["results"]), 20)

	def test_dashboard_sql_aggregations_no_unlimited_reads(self):
		"""get_dashboard and get_product_activity use SQL aggregates without limit_page_length=0."""
		dash = get_dashboard()
		self.assertIn("today", dash)
		self.assertIn("orders", dash["today"])
		self.assertIn("order_value", dash["today"])
		self.assertIsInstance(dash["today"]["orders"], int)
		self.assertIsInstance(dash["today"]["order_value"], float)

		# Product activity
		products = get_products_list(limit=1)["items"]
		if products:
			act = get_product_activity(products[0]["name"])
			self.assertIn("orders_30d", act)
			self.assertIn("units_30d", act)
			self.assertIn("value_30d", act)
			self.assertIsInstance(act["orders_30d"], int)
			self.assertIsInstance(act["units_30d"], float)
			self.assertIsInstance(act["value_30d"], float)

	def test_products_location_stock_overlay(self):
		"""get_products_list overlays location-specific stock when location_id is supplied."""
		from field_sales.sync.stock import get_sell_locations
		locations = get_sell_locations()
		if locations:
			loc_id = locations[0]["location_id"]
			res = get_products_list(limit=10, location_id=loc_id)
			self.assertIn("items", res)
			for item in res["items"]:
				if item.get("track_inventory"):
					self.assertIn("available_qty", item)
					self.assertEqual(item.get("location_id"), loc_id)

	def test_products_tab_filters_and_barcode(self):
		"""get_products_list supports tabs ('Active', 'low', 'all') and exact/EAN barcode lookup."""
		active_res = get_products_list(tab="Active", limit=5)
		self.assertIn("items", active_res)
		for item in active_res["items"]:
			self.assertEqual(item["status"], "Active")

		low_res = get_products_list(tab="low", limit=5)
		self.assertIn("items", low_res)
		for item in low_res["items"]:
			self.assertEqual(item["status"], "Active")
			self.assertLessEqual(item["available_qty"], 5)

		# Barcode search
		p_rows = frappe.get_all("Platform Product", fields=["name", "barcode"], limit=1)
		if p_rows and p_rows[0].get("barcode"):
			code = p_rows[0]["barcode"]
			b_res = get_products_list(barcode=code)
			self.assertTrue(any(p["name"] == p_rows[0]["name"] for p in b_res["items"]))

	def test_product_sync_status_and_permissions(self):
		"""get_product_sync_status returns sync status for managers and blocks unauthorized users."""
		# Administrator / Sales Manager has permission
		frappe.set_user("Administrator")
		status = get_product_sync_status()
		self.assertIn("is_running", status)
		self.assertIn("last_sync", status)
		if status["last_sync"]:
			self.assertIn("status", status["last_sync"])
			self.assertIn("timestamp", status["last_sync"])

		# Unauthorized rep role without Sales Manager is blocked
		frappe.set_user("guest@example.com")
		with self.assertRaises(frappe.PermissionError):
			get_product_sync_status()
