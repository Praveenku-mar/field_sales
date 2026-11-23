# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The main flows on the shared multi-store site (Phase 8): each runs in store A while store B holds
its own records, and B must come out untouched. The isolation suite (test_store_isolation.py)
covers reads, endpoints and jobs; this covers whole flows that write.

Shopify is never called: the connector is a mock.
"""

from unittest.mock import MagicMock, patch

import frappe

from field_sales.tests.test_store_isolation import MULTI, StoreIsolationCase
from field_sales.utils.store import current_store, use_store

# Store B's records and the fields a flow in store A might wrongly change.
WATCHED = {
	"Field Order": ("order", ["docstatus", "sync_status", "external_order_id", "payment_status", "modified"]),
	"Retailer": ("retailer", ["external_customer_id", "email", "latitude", "longitude", "modified"]),
	"Visit": ("visit", ["status", "check_in_time", "modified"]),
	"Platform Product": ("product", ["status", "price", "modified"]),
}


class SharedSiteFlowCase(StoreIsolationCase):
	def setUp(self):
		super().setUp()
		frappe.db.savepoint("shared_site_flows")
		self.multi = patch.dict(frappe.conf, MULTI)
		self.multi.start()
		self.addCleanup(self.multi.stop)
		self.b_before = self.snapshot(self.b)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="shared_site_flows")
		super().tearDown()

	@staticmethod
	def snapshot(team) -> dict:
		return {
			doctype: frappe.db.get_value(doctype, team[key], fields, as_dict=True)
			for doctype, (key, fields) in WATCHED.items()
		}

	def assert_store_b_untouched(self):
		self.assertEqual(self.snapshot(self.b), self.b_before)


class TestOrderFlows(SharedSiteFlowCase):
	def test_an_order_is_numbered_dated_and_priced_in_its_store(self):
		from field_sales.api import create_order
		from field_sales.utils.money import store_currency
		from field_sales.utils.store import store_today

		frappe.db.set_value("Store", "ISA", "shop_currency", "INR")
		self.as_user(self.a.rep_user)
		order = create_order(self.a.retailer, self.a.rep, str(store_today("ISA")))

		self.assertEqual(order["store"], "ISA")
		self.assertTrue(order["name"].startswith("FO-ISA-"), order["name"])
		self.assertEqual(order["currency"], store_currency("ISA"))
		self.assert_store_b_untouched()

	def test_sending_an_order_uses_its_own_stores_connection(self):
		from field_sales.sync import orders
		from field_sales.utils.store import run_in_store

		frappe.db.set_value("Field Order", self.a.order, {"docstatus": 1, "sync_status": "Pending"})
		used_in = []
		conn = MagicMock()
		conn.settings = frappe._dict(platform="Shopify", order_mode="Draft")
		conn.create_order.return_value = {"draft_id": "D-ISA-1", "draft_name": "#D1"}

		def connector(*args, **kwargs):
			used_in.append(current_store())
			return conn

		with (
			patch.object(orders, "get_connector", side_effect=connector),
			patch.object(orders, "_ensure_store_customer"),
			patch.object(orders, "_notify"),
			patch.object(orders, "safe_commit"),
		):
			# As the queued job runs it: inside the order's store, as nobody in particular.
			frappe.set_user("Administrator")
			run_in_store("field_sales.sync.orders.push_order", "ISA", field_order=self.a.order)

		self.assertEqual(used_in, ["ISA"])
		self.assertEqual(frappe.db.get_value("Field Order", self.a.order, "external_draft_id"), "D-ISA-1")
		self.assert_store_b_untouched()

	def test_an_order_event_of_one_shop_never_reaches_another_stores_order(self):
		from field_sales.commission.engine import reconcile_order
		from field_sales.payments.store_payments import apply_store_payment

		frappe.db.set_value(
			"Field Order", self.a.order, {"docstatus": 1, "external_order_id": "7001", "sync_status": "Synced"}
		)
		before = frappe.db.get_value("Field Order", self.a.order, "payment_status")
		conn = MagicMock()
		with use_store("ISB"):
			commission = reconcile_order("7001")
			payment = apply_store_payment(conn, "7001")

		self.assertIn("Ignored", commission["note"])
		self.assertIn("Ignored", payment["note"])
		conn.get_order_payment.assert_not_called()
		self.assertEqual(frappe.db.get_value("Field Order", self.a.order, "payment_status"), before)


class TestRetailerFlows(SharedSiteFlowCase):
	def test_a_customer_import_matches_retailers_of_its_own_store_only(self):
		from field_sales.sync import customers

		# Store B already has a retailer with this email; store A imports a customer with it.
		frappe.db.set_value("Retailer", self.b.retailer, "email", "shared.buyer@example.com")
		self.b_before = self.snapshot(self.b)
		conn = MagicMock()
		conn.settings = frappe._dict(platform="Shopify")
		conn.get_customers.return_value = {
			"items": [
				{
					# As the connector normalizes a store customer.
					"external_customer_id": "C-ISA-1",
					"first_name": "Shared",
					"last_name": "Buyer",
					"company": "Shared Buyer Stores",
					"email": "shared.buyer@example.com",
					"phone": "9876500001",
					"address_line1": "2 Market Street",
					"city": "Coimbatore",
				}
			],
			"next_cursor": None,
		}
		with (
			use_store("ISA"),
			patch.object(customers, "get_connector", return_value=conn),
			patch.object(customers, "enqueue_push"),
			patch.object(customers, "safe_commit"),
		):
			summary = customers.import_customers()

		self.assertEqual(summary.get("created"), 1, summary)
		new = frappe.db.get_value("Retailer", {"external_customer_id": "C-ISA-1"}, ["store", "email"], as_dict=True)
		self.assertEqual(new.store, "ISA")
		self.assert_store_b_untouched()

	def test_a_check_in_changes_only_its_own_store(self):
		from field_sales.api import check_in

		self.as_user(self.a.rep_user)
		result = check_in(self.a.visit, 11.0168, 76.9558, 10)

		self.assertEqual(frappe.db.get_value("Visit", self.a.visit, "status"), "In Progress", result)
		self.assert_store_b_untouched()
		# And never into another store's visit.
		self.assertRaises(frappe.PermissionError, check_in, self.b.visit, 11.0168, 76.9558, 10)


class TestMobileFlows(SharedSiteFlowCase):
	def test_a_reps_mobile_lists_hold_only_their_store(self):
		from field_sales.api import mobile

		self.as_user(self.a.manager)
		lists = {
			"Field Order": mobile.get_orders_list(),
			"Visit": mobile.get_visits_list(),
			"Retailer": mobile.get_customers_list(),
			"Platform Product": mobile.get_products_list(),
		}
		for doctype, page in lists.items():
			names = [row.get("name") for row in page.get("items", page.get("data", []))]
			with self.subTest(doctype=doctype):
				self.assertTrue(names, f"no {doctype} rows")
				stores = set(frappe.get_all(doctype, filters={"name": ("in", names)}, pluck="store"))
				self.assertEqual(stores, {"ISA"})


class TestCrossStoreLinks(SharedSiteFlowCase):
	"""A store A record can never point at store B's records, whatever ids its user knows."""

	def new_order(self, **values):
		from field_sales.utils.store import store_today

		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.a.retailer,
				"sales_rep": self.a.rep,
				"order_date": store_today("ISA"),
				"items": [{"product": self.a.product, "qty": 1, "rate": 100}],
				**values,
			}
		)
		doc.flags.ignore_links = False
		return doc

	def test_an_order_cannot_use_another_stores_retailer_visit_or_product(self):
		self.as_user(self.a.rep_user)
		cases = {
			"retailer": {"retailer": self.b.retailer},
			"visit": {"visit": self.b.visit},
			"product": {"items": [{"product": self.b.product, "qty": 1, "rate": 100}]},
		}
		for label, values in cases.items():
			# The visit is refused first by the order's own rule (a visit to another retailer).
			with self.subTest(link=label), self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				self.new_order(**values).insert()
		self.assert_store_b_untouched()

	def test_a_saved_order_cannot_take_another_stores_product_later(self):
		from field_sales.sync import orders

		self.as_user(self.a.rep_user)
		order = self.new_order().insert()
		order.reload()
		order.append("items", {"product": self.b.product, "qty": 1, "rate": 100})
		with patch.object(orders, "enqueue_push_order") as push:
			self.assertRaises(frappe.PermissionError, order.save)
			# Submitting it (a fresh copy: the failed save stamped this one) is refused the same way.
			order = frappe.get_doc("Field Order", order.name)
			order.append("items", {"product": self.b.product, "qty": 1, "rate": 100})
			self.assertRaises(frappe.PermissionError, order.submit)
		push.assert_not_called()
		self.assert_store_b_untouched()

	def test_a_visit_cannot_go_to_another_stores_retailer(self):
		from field_sales.utils.store import store_today

		self.as_user(self.a.rep_user)
		visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.a.rep,
				"retailer": self.b.retailer,
				"visit_date": store_today("ISA"),
				"status": "Planned",
			}
		)
		self.assertRaises(frappe.PermissionError, visit.insert)
		self.assert_store_b_untouched()


class TestOrderCurrency(SharedSiteFlowCase):
	"""An order's currency is its store's: a client cannot choose it, on insert or later."""

	def test_a_rep_cannot_choose_an_orders_currency(self):
		from field_sales.connectors import ConnectorError, get_connector
		from field_sales.utils.store import store_today

		for code in ("EUR",):
			if not frappe.db.exists("Currency", code):
				frappe.get_doc({"doctype": "Currency", "currency_name": code, "enabled": 1}).insert()
		frappe.db.set_value("Store", "ISA", "shop_currency", "INR")
		self.as_user(self.a.rep_user)
		order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"currency": "EUR",  # sent by the client
				"retailer": self.a.retailer,
				"sales_rep": self.a.rep,
				"order_date": store_today("ISA"),
				"items": [{"product": self.a.product, "qty": 1, "rate": 100}],
			}
		).insert()
		self.assertEqual(order.currency, "INR")

		order.reload()
		order.currency = "EUR"
		order.save()
		self.assertEqual(frappe.db.get_value("Field Order", order.name, "currency"), "INR")

		# The shop has since moved to EUR: the push is refused, prices are never sent as EUR.
		frappe.set_user("Administrator")
		conn = get_connector(require_enabled=False, store="ISA")
		conn._currency = "EUR"
		with self.assertRaises(ConnectorError) as raised:
			conn._get_currency(frappe.get_doc("Field Order", order.name))
		self.assertEqual(raised.exception.code, "CURRENCY_MISMATCH")
