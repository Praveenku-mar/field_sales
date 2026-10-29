# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Unit tests for the Odoo connector's platform-independent logic
(normalization, URL handling, topic mapping, event handlers, order-line
building, draft mapping). No network: the JSON-RPC layer is stubbed."""

import frappe
from frappe.tests import IntegrationTestCase

import field_sales.connectors.odoo as connector_module
from field_sales.connectors.base import ConnectorError
from field_sales.connectors.odoo import (
	DONE_STATES,
	STOCK_LOCATION_ID,
	OdooConnector,
	_map_topic,
	_normalize_order,
	_order_lines,
	_order_note,
	_partner_values,
	_variant_title,
)


def make_connector(order_mode: str = "Draft") -> OdooConnector:
	"""A connector built from a scratch settings doc; nothing is fetched."""
	settings = frappe.new_doc("Store Connection")
	settings.platform = "Odoo"
	settings.store_url = "https://store.example"
	settings.odoo_database = "db1"
	settings.odoo_username = "someone@example.com"
	settings.odoo_api_key = "key_secret"
	settings.order_mode = order_mode
	return OdooConnector(settings)


class TestNormalizeStoreUrl(IntegrationTestCase):
	def test_adds_scheme_and_strips_slash(self):
		self.assertEqual(
			OdooConnector.normalize_store_url("store.example/"),
			"https://store.example",
		)
		self.assertEqual(
			OdooConnector.normalize_store_url("http://store.example/"),
			"http://store.example",
		)

	def test_empty_url_throws(self):
		self.assertRaises(frappe.ValidationError, OdooConnector.normalize_store_url, "  ")


class TestConstruction(IntegrationTestCase):
	def test_missing_credentials_throw(self):
		settings = frappe.new_doc("Store Connection")
		settings.platform = "Odoo"
		settings.store_url = "https://store.example"
		settings.odoo_username = "someone@example.com"
		settings.odoo_api_key = "key_secret"
		self.assertRaises(frappe.ValidationError, OdooConnector, settings)


class TestNormalizeProduct(IntegrationTestCase):
	def test_simple_product(self):
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 12, "name": "Table", "product_tmpl_id": [9, "Table"], "type": "product",
			 "lst_price": 2500, "default_code": "TBL", "barcode": "890123", "categ_id": [4, "Furniture"],
			 "qty_available": 97, "is_published": True}
		)
		self.assertEqual(item["external_product_id"], "9")
		self.assertEqual(item["external_variant_id"], "12")
		self.assertEqual(item["external_inventory_id"], "12")
		self.assertEqual(item["product_title"], "Table")
		self.assertIsNone(item["variant_title"])
		self.assertEqual(item["sku"], "TBL")
		self.assertEqual(item["barcode"], "890123")
		self.assertEqual(item["price"], 2500)
		self.assertIsNone(item["compare_at_price"])
		self.assertEqual(item["collection"], "Furniture")
		self.assertEqual(
			item["image_url"], "https://store.example/web/image/product.product/12/image_1920"
		)
		self.assertEqual(item["status"], "Active")
		self.assertTrue(item["track_inventory"])

	def test_variant_display_name_and_draft_status(self):
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 31, "name": "Chair (Red)", "product_tmpl_id": [29, "Chair"], "type": "consu",
			 "is_storable": True, "lst_price": 550, "is_published": False}
		)
		self.assertEqual(item["variant_title"], "Red")
		self.assertEqual(item["status"], "Draft")
		self.assertTrue(item["track_inventory"])  # Odoo 17+: Goods with the storable flag

	def test_non_storable_goods_do_not_track_stock(self):
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 32, "name": "Napkin", "product_tmpl_id": [30, "Napkin"], "type": "consu",
			 "is_storable": False, "lst_price": 5}
		)
		self.assertFalse(item["track_inventory"])

	def test_without_is_published_field_defaults_active(self):
		# A plain Odoo sale install has no website_sale: no is_published at all.
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 12, "name": "Table", "product_tmpl_id": [9, "Table"], "type": "product", "lst_price": 10}
		)
		self.assertEqual(item["status"], "Active")


class TestSearchProducts(IntegrationTestCase):
	def test_is_published_fallback_when_field_missing(self):
		conn = make_connector()
		requests = []

		def fake(model, domain, fields, operation, **kwargs):
			requests.append(fields)
			if "is_published" in fields:
				raise ConnectorError(
					"Invalid field 'is_published' on model 'product.product'", code="BAD_FIELD"
				)
			return [{"id": 5, "name": "Chair", "product_tmpl_id": [3, "Chair"], "type": "product"}]

		conn._search_read = fake
		rows = conn._search_products([], "Product Sync")
		self.assertEqual(rows[0]["id"], 5)
		self.assertFalse(conn._has_is_published)
		conn._search_products([], "Product Sync")
		self.assertNotIn("is_published", requests[-1])  # probed once, never again

	def test_other_errors_are_not_swallowed(self):
		conn = make_connector()

		def fake(model, domain, fields, operation, **kwargs):
			raise ConnectorError("AccessDenied", code="AUTH_FAILED")

		conn._search_read = fake
		self.assertRaises(ConnectorError, conn._search_products, [], "Product Sync")
		self.assertIsNone(conn._has_is_published)


class TestGetStock(IntegrationTestCase):
	def test_non_numeric_ids_are_zero_without_network(self):
		conn = make_connector()
		self.assertEqual(conn.get_stock(["", "x"], "1"), {"": 0, "x": 0})

	def test_reads_qty_scoped_to_the_warehouse_location(self):
		conn = make_connector()
		conn._warehouse_locations = {"2": 77}
		seen = {}

		def fake(model, domain, fields, operation, ref=None, context=None, **kwargs):
			seen["context"] = context
			seen["domain"] = domain
			return [{"id": 9, "qty_available": 4.0}]

		conn._search_read = fake
		self.assertEqual(conn.get_stock(["9", "10"], "2"), {"9": 4, "10": 0})
		self.assertEqual(seen["context"], {"location": 77})  # warehouse 2 -> its stock location
		# every id is asked for at once; rows the store does not return map to 0
		self.assertEqual(seen["domain"], [("id", "in", [9, 10])])


class TestLocations(IntegrationTestCase):
	def test_store_without_inventory_app_gets_the_pseudo_pool(self):
		# An eCommerce-only Odoo has no stock.warehouse at all.
		conn = make_connector()

		def fake(model, domain, fields, operation, ref=None, **kwargs):
			raise ConnectorError("Object stock.warehouse doesn't exist", code="NOT_FOUND")

		conn._search_read = fake
		self.assertEqual(
			conn.get_locations(), [{"id": "odoo", "name": "Odoo Stock"}]
		)
		# stock reads on the pseudo pool stay company-wide: no location context
		conn._search_read = lambda *a, **kw: [{"id": 9, "qty_available": 2}]
		self.assertEqual(conn.get_stock(["9"], STOCK_LOCATION_ID), {"9": 2})

	def test_other_warehouse_errors_still_raise(self):
		conn = make_connector()

		def fake(model, domain, fields, operation, ref=None, **kwargs):
			raise ConnectorError("AccessDenied", code="AUTH_FAILED")

		conn._search_read = fake
		self.assertRaises(ConnectorError, conn.get_locations)


class TestGetProductsPagination(IntegrationTestCase):
	def test_cursor_is_offset(self):
		conn = make_connector()
		conn._search_products = lambda domain, operation, limit=None, offset=None: [
			{"id": i, "name": f"P{i}", "product_tmpl_id": [i, f"P{i}"], "type": "product", "lst_price": 1}
			for i in range(offset, offset + limit)
		]
		page = conn.get_products("0")
		self.assertEqual(len(page["items"]), 100)
		self.assertEqual(page["next_cursor"], "100")
		page = conn.get_products("100")
		self.assertEqual(page["items"][0]["external_variant_id"], "100")


class TestDrafts(IntegrationTestCase):
	def test_completed_drafts_and_names_split_by_state(self):
		conn = make_connector()
		conn._read = lambda model, ids, fields, operation, ref=None: [
			{"id": 1, "name": "S0001", "state": "sale"},
			{"id": 2, "name": "S0002", "state": "draft"},
			{"id": 3, "name": "S0003", "state": "cancel"},
			{"id": 4, "name": "S0004", "state": "done"},
		]
		# A draft confirmed in Odoo keeps its id: draft id == order id.
		self.assertEqual(conn.get_completed_drafts(["1", "2", "3", "4"]), {"1": "1", "4": "4"})
		self.assertEqual(conn.get_draft_names(["1", "2", "3", "4"]), {"2": "S0002"})


class TestCreateOrder(IntegrationTestCase):
	def test_existing_order_is_reused_not_duplicated(self):
		conn = make_connector()
		doc = frappe.new_doc("Field Order")
		doc.external_order_id = "55"
		created = []
		conn._read = lambda model, ids, fields, operation, ref=None: [{"name": "S0055"}]
		conn._execute_kw = lambda *a, **kw: created.append(1)
		conn._find_order_by_ref = lambda ref, operation: None

		result = conn.create_order(doc)
		self.assertEqual(result["order_id"], "55")
		self.assertEqual(result["order_name"], "S0055")
		self.assertFalse(created)  # nothing was created again

	def test_failed_confirm_keeps_the_draft_id(self):
		conn = make_connector(order_mode="Confirmed")
		doc = frappe.new_doc("Field Order")
		doc.name = "ORD-TEST-1"

		def execute_kw(model, method, args, *a, **kw):
			if method == "create":
				return 91
			if method == "action_confirm":
				raise ConnectorError("Not enough stock", code="ODOO_ERROR")
			return None

		conn._execute_kw = execute_kw
		conn._find_order_by_ref = lambda ref, operation: None
		conn._read = lambda model, ids, fields, operation, ref=None: [{"name": "S0091"}]
		# The customer/values plumbing is other code's concern here: only the
		# confirm step's failure handling is under test.
		original_values = connector_module._order_values
		connector_module._order_values = lambda field_order: {"partner_id": 7}
		self.addCleanup(setattr, connector_module, "_order_values", original_values)

		with self.assertRaises(ConnectorError) as ctx:
			conn.create_order(doc)
		self.assertEqual(ctx.exception.partial.get("draft_id"), "91")


class TestMarkOrderPaid(IntegrationTestCase):
	def _order(self, external_id="7"):
		doc = frappe.new_doc("Field Order")
		doc.external_order_id = external_id
		return doc

	def test_missing_order_raises_not_found(self):
		conn = make_connector()
		conn._sale_payment_state = True
		conn._read = lambda model, ids, fields, operation, ref=None: []
		with self.assertRaises(ConnectorError) as ctx:
			conn.mark_order_paid(self._order())
		self.assertEqual(ctx.exception.code, "NOT_FOUND")

	def test_already_paid_left_alone(self):
		conn = make_connector()
		conn._sale_payment_state = True
		conn._read = lambda model, ids, fields, operation, ref=None: [{"payment_state": "paid"}]
		written = []
		conn._execute_kw = lambda *a, **kw: written.append(1)
		result = conn.mark_order_paid(self._order())
		self.assertTrue(result["already_paid"])
		self.assertFalse(written)

	def test_write_failure_becomes_cannot_mark_paid(self):
		conn = make_connector()
		conn._sale_payment_state = True
		conn._read = lambda model, ids, fields, operation, ref=None: [{"payment_state": "not_paid"}]

		def execute_kw(*a, **kw):
			raise ConnectorError("denied", code="ODOO_ERROR")

		conn._execute_kw = execute_kw
		with self.assertRaises(ConnectorError) as ctx:
			conn.mark_order_paid(self._order())
		self.assertEqual(ctx.exception.code, "CANNOT_MARK_PAID")

	def test_odoo_without_payment_state_uses_transactions(self):
		# Odoo 18+ dropped sale.order.payment_state.
		conn = make_connector()
		conn._sale_payment_state = False
		conn._transaction_field = "sale_order_id"  # link field name probed separately
		conn._search_read = (
			lambda model, domain, fields, operation, ref=None, **kw: [{"state": "done"}]
		)
		self.assertEqual(
			conn.mark_order_paid(self._order()), {"financial_status": "paid", "already_paid": True}
		)
		# no gateway payment there either: an honest, logged refusal
		conn._search_read = lambda model, domain, fields, operation, ref=None, **kw: []
		with self.assertRaises(ConnectorError) as ctx:
			conn.mark_order_paid(self._order())
		self.assertEqual(ctx.exception.code, "CANNOT_MARK_PAID")


class TestOrderLines(IntegrationTestCase):
	def _order(self, discount: float = 0, tax: float = 0):
		doc = frappe.new_doc("Field Order")
		doc.name = "ORD-1"
		doc.discount_amount = discount
		doc.tax_percent = tax
		doc.append("items", {"product": "TP-1", "external_variant_id": "9", "qty": 2, "rate": 100})
		doc.append("items", {"product": "TP-2", "external_variant_id": "11", "qty": 1, "rate": 500})
		return doc

	def test_lines_carry_price_qty_and_no_tax(self):
		lines = _order_lines(self._order())
		self.assertEqual(len(lines), 2)
		cmd, _, first = lines[0]
		self.assertEqual(cmd, 0)
		self.assertEqual(first["product_id"], 9)
		self.assertEqual(first["product_uom_qty"], 2)
		self.assertEqual(first["price_unit"], 100)
		self.assertEqual(first["tax_ids"], [(5, 0, 0)])  # Odoo must not re-tax the lines
		self.assertEqual(first["discount"], 0)

	def test_flat_discount_becomes_one_percent_on_every_line(self):
		# gross = 2*100 + 1*500 = 700; discount 70 -> 10% everywhere
		lines = _order_lines(self._order(discount=70))
		self.assertEqual(lines[0][2]["discount"], 10)
		self.assertEqual(lines[1][2]["discount"], 10)

	def test_no_store_items_throw(self):
		doc = self._order()
		doc.items = []
		self.assertRaises(frappe.ValidationError, _order_lines, doc)


class TestOrderNote(IntegrationTestCase):
	def test_note_has_order_no_rep_and_tax_line(self):
		doc = frappe.new_doc("Field Order")
		doc.name = "ORD-9"
		doc.sales_rep = "Rep A"
		doc.tax_percent = 5
		note = _order_note(doc)
		self.assertIn("ORD-9", note)
		self.assertIn("Rep A", note)
		self.assertIn("5%", note)


class TestPartnerValues(IntegrationTestCase):
	def _retailer(self):
		retailer = frappe.new_doc("Retailer")
		retailer.retailer_name = "Kovai Mart"
		retailer.email = "kovai@example.com"
		retailer.phone = "9876543210"
		retailer.address_line1 = "12 MG Road"
		retailer.city = "Coimbatore"
		retailer.state = "Tamil Nadu"
		retailer.pincode = "641001"
		retailer.country = "IN"
		return retailer

	def test_fields_map_and_country_resolved(self):
		values = _partner_values(self._retailer(), "kovai@example.com", country_id=105, state_id=7)
		self.assertEqual(values["name"], "Kovai Mart")
		self.assertEqual(values["street"], "12 MG Road")
		self.assertEqual(values["zip"], "641001")
		self.assertEqual(values["country_id"], 105)
		self.assertEqual(values["state_id"], 7)
		self.assertEqual(values["customer_rank"], 1)

	def test_missing_address_parts_become_false_not_empty_strings(self):
		retailer = frappe.new_doc("Retailer")
		retailer.retailer_name = "Bare"
		values = _partner_values(retailer, "bare@x.example", country_id=False)
		self.assertFalse(values["phone"])
		self.assertFalse(values["city"])
		self.assertFalse(values["country_id"])


class TestNormalizeOrder(IntegrationTestCase):
	def _order(self, **overrides):
		order = {
			"id": 25, "currency_id": [12, "INR"], "state": "sale", "payment_state": "not_paid",
			"order_line": [61],
		}
		order.update(overrides)
		return order

	def _lines(self, **overrides):
		line = {"id": 61, "product_id": [9, "Chair"], "product_uom_qty": 2, "qty_delivered": 0,
			"price_unit": 100.0, "price_subtotal": 180.0}
		line.update(overrides)
		return [line]

	def test_unpaid_confirmed_with_line_discount(self):
		result = _normalize_order(self._order(), self._lines())
		self.assertEqual(result["external_order_id"], "25")
		self.assertEqual(result["currency"], "INR")
		self.assertEqual(result["financial_status"], "pending")
		self.assertEqual(result["fulfillment_status"], "unfulfilled")
		self.assertFalse(result["cancelled"])
		line = result["lines"][0]
		self.assertEqual(line["qty"], 2)
		self.assertEqual(line["unit_price"], 100)
		self.assertEqual(line["discount"], 20)  # 2*100 - 180
		self.assertEqual(line["external_variant_id"], "9")
		self.assertEqual(result["refunds"], [])

	def test_paid_locked_and_delivered(self):
		result = _normalize_order(
			self._order(state="done", payment_state="paid"), self._lines(qty_delivered=2)
		)
		self.assertEqual(result["financial_status"], "paid")
		self.assertEqual(result["fulfillment_status"], "fulfilled")

	def test_partially_delivered_and_in_payment(self):
		result = _normalize_order(
			self._order(payment_state="in_payment"), self._lines(qty_delivered=1)
		)
		self.assertEqual(result["financial_status"], "paid")
		self.assertEqual(result["fulfillment_status"], "partial")

	def test_cancelled_and_reversed(self):
		result = _normalize_order(self._order(state="cancel"), self._lines())
		self.assertTrue(result["cancelled"])
		result = _normalize_order(self._order(payment_state="reversed"), self._lines())
		self.assertEqual(result["financial_status"], "refunded")

	def test_financial_override_for_stores_without_payment_state(self):
		result = _normalize_order(self._order(), self._lines(), financial="paid")
		self.assertEqual(result["financial_status"], "paid")


class TestEvents(IntegrationTestCase):
	def test_webhooks_are_refused(self):
		# Odoo cannot sign webhooks; nothing unverified may be accepted.
		self.assertFalse(make_connector().verify_webhook({}, b"{}"))

	def test_stock_event(self):
		conn = make_connector()
		conn.settings.default_location_id = "2"
		stock = conn.handle_stock_event({"id": 29, "qty_available": 3})
		self.assertEqual(stock, {"external_inventory_id": "29", "location_id": "2", "available": 3})
		self.assertIsNone(conn.handle_stock_event({"id": 29}))
		self.assertIsNone(conn.handle_stock_event({"qty_available": 5}))

	def test_product_event_and_order_id(self):
		conn = make_connector()
		variants = conn.handle_product_event({"id": 29, "lst_price": 15})
		self.assertEqual(variants, [{"external_variant_id": "29", "price": 15, "compare_at_price": None}])
		self.assertIsNone(conn.handle_product_event({}))
		self.assertEqual(conn.order_id_from_event({"id": 33}), "33")
		self.assertIsNone(conn.order_id_from_event({}))

	def test_topics_map(self):
		cases = [
			("product.update", "product/update"),
			("product.delete", "product/delete"),
			("order.paid", "order/paid"),
			("order.done", "order/fulfilled"),
			("order.cancel", "order/cancelled"),
			("whatever", "whatever"),
		]
		for topic, expected in cases:
			with self.subTest(topic=topic):
				self.assertEqual(_map_topic(topic, {}), expected)


class TestVariantTitle(IntegrationTestCase):
	def test_product_title_drops_the_bracketed_code(self):
		conn = make_connector()
		item = conn._normalize_product(
			{"id": 12, "name": "[TBL] Table", "product_tmpl_id": [9, "[TBL] Table"], "type": "consu",
			 "default_code": "TBL", "lst_price": 10}
		)
		self.assertEqual(item["product_title"], "Table")
		self.assertEqual(item["sku"], "TBL")

	def test_parses_template_suffix(self):
		self.assertEqual(_variant_title("Chair (Red)", "Chair"), "Red")
		self.assertEqual(_variant_title("Chair (Red) (XL)", "Chair"), "Red) (XL")

	def test_no_variant_or_mismatch(self):
		self.assertIsNone(_variant_title("Chair", "Chair"))
		self.assertIsNone(_variant_title("", "Chair"))
		self.assertEqual(_variant_title("Red Chair", "Chair"), "Red Chair")


class TestDoneStates(IntegrationTestCase):
	def test_cancel_is_not_completed(self):
		self.assertNotIn("cancel", DONE_STATES)


class TestOrderUrl(IntegrationTestCase):
	def test_backend_url(self):
		conn = make_connector()
		self.assertEqual(
			conn.get_order_url("25"), "https://store.example/odoo/sale.order/25"
		)
		self.assertEqual(conn.get_order_url(None, "7"), "https://store.example/odoo/sale.order/7")
		self.assertIsNone(conn.get_order_url(None))
