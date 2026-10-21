# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from field_sales.connectors.base import ConnectorError
from field_sales.connectors.shopify import ORDER_MARK_AS_PAID, ORDER_PAYMENT_STATE, ShopifyConnector
from field_sales.payments import razorpay
from field_sales.sync import orders
from field_sales.sync.orders import mark_store_order_paid, retry_store_payments
from field_sales.tests import test_razorpay_links as links


class TestStoreMarkPaid(IntegrationTestCase):
	"""A Razorpay payment marks the confirmed Shopify order paid."""

	_user = links.TestRazorpayLinks._user
	order_values = links.TestRazorpayLinks.order_values

	def setUp(self):
		links.TestRazorpayLinks.setUp(self)
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": "test-shop.myshopify.com",
				"shopify_api_version": "2026-10",
				"order_mode": "Confirmed",
			},
		)
		frappe.db.set_value(
			"Field Order",
			self.order.name,
			{
				"external_order_id": "5550001",
				"store_order_name": "#2001",
				"payment_status": "Paid",
				"amount_paid": 10,
				"paid_on": now_datetime(),
			},
		)
		self.calls = []

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def shopify(self, status="PENDING", can_mark=True):
		"""Fake ``_graphql``: answers the payment-state query and the mutation."""

		def graphql(conn, query, variables=None, operation="Other", ref=None):
			self.calls.append((query, variables, operation))
			if query == ORDER_PAYMENT_STATE:
				order = {"id": variables["id"], "name": "#2001", "displayFinancialStatus": status, "canMarkAsPaid": can_mark}
				return {"order": order}
			if query == ORDER_MARK_AS_PAID:
				return {"orderMarkAsPaid": {"order": {"id": variables["input"]["id"], "displayFinancialStatus": "PAID"}}}
			raise AssertionError("unexpected query")

		return patch.object(ShopifyConnector, "_graphql", autospec=True, side_effect=graphql)

	def mutations(self):
		return [c for c in self.calls if c[0] == ORDER_MARK_AS_PAID]

	def marked_on(self):
		return frappe.db.get_value("Field Order", self.order.name, "store_marked_paid_on")

	# -- the job ---------------------------------------------------------------

	def test_marks_store_order_paid_once(self):
		with self.shopify():
			mark_store_order_paid(self.order.name)
		(mutation,) = self.mutations()
		self.assertEqual(mutation[1], {"input": {"id": "gid://shopify/Order/5550001"}})
		self.assertEqual(mutation[2], "Mark Order Paid")
		self.assertIsNotNone(self.marked_on())
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_name": self.order.name, "content": ("like", "%#2001 marked as paid%")})
		)

		with self.shopify():
			mark_store_order_paid(self.order.name)
		self.assertEqual(len(self.calls), 2)  # nothing more after it was done

	def test_already_paid_on_store_is_not_marked_again(self):
		with self.shopify(status="PAID", can_mark=False):
			mark_store_order_paid(self.order.name)
		self.assertEqual(self.mutations(), [])
		self.assertIsNotNone(self.marked_on())

	def test_store_refusal_is_logged_and_retried_later(self):
		with self.shopify(status="VOIDED", can_mark=False):
			mark_store_order_paid(self.order.name)
		self.assertEqual(self.mutations(), [])
		self.assertIsNone(self.marked_on())
		self.assertTrue(
			frappe.db.exists(
				"Sync Log",
				{"operation": "Mark Order Paid", "status": "Failed", "error_code": "CANNOT_MARK_PAID", "reference_name": self.order.name},
			)
		)

	def test_api_error_leaves_it_for_retry(self):
		with patch.object(ShopifyConnector, "_graphql", side_effect=ConnectorError("down", code="NETWORK_ERROR")):
			mark_store_order_paid(self.order.name)
		self.assertIsNone(self.marked_on())

	def test_skips_orders_that_should_not_be_marked(self):
		cases = {
			"draft order": {"external_draft_id": "777"},
			"not pushed": {"external_order_id": None},
			"partly paid": {"payment_status": "Partially Paid"},
			"cancelled": {"docstatus": 2},
		}
		for label, values in cases.items():
			with self.subTest(label):
				frappe.db.savepoint("skip")
				frappe.db.set_value("Field Order", self.order.name, values)
				with self.shopify():
					mark_store_order_paid(self.order.name)
				self.assertEqual(self.calls, [])
				frappe.db.rollback(save_point="skip")

	def test_store_connection_disabled_does_nothing(self):
		frappe.db.set_single_value("Store Connection", "enabled", 0)
		with self.shopify():
			mark_store_order_paid(self.order.name)
		self.assertEqual(self.calls, [])
		self.assertIsNone(self.marked_on())

	# -- triggers --------------------------------------------------------------

	def test_full_razorpay_payment_queues_it(self):
		frappe.db.set_value("Field Order", self.order.name, {"payment_status": "Link Sent", "amount_paid": 0})
		doc = frappe.get_doc("Field Order", self.order.name)
		paise = round(doc.grand_total * 100)
		with patch.object(orders, "enqueue_mark_store_paid") as enqueue:
			razorpay._apply_link_state(doc, {"id": "plink_1", "status": "partially_paid", "amount_paid": 100})
			enqueue.assert_not_called()
			razorpay._apply_link_state(doc, {"id": "plink_1", "status": "paid", "amount_paid": paise})
		enqueue.assert_called_once_with(self.order.name)

	def test_hourly_retry_queues_unmarked_paid_orders(self):
		with patch.object(orders, "enqueue_mark_store_paid") as enqueue:
			retry_store_payments()
		self.assertIn(self.order.name, [c.args[0] for c in enqueue.call_args_list])

		frappe.db.set_value("Field Order", self.order.name, "store_marked_paid_on", now_datetime())
		with patch.object(orders, "enqueue_mark_store_paid") as enqueue:
			retry_store_payments()
		self.assertNotIn(self.order.name, [c.args[0] for c in enqueue.call_args_list])

	# -- order page sync card --------------------------------------------------

	def test_sync_info_links_the_order_and_its_draft(self):
		from field_sales.frontend_api import get_order_sync_info

		info = get_order_sync_info(self.order.name)
		self.assertEqual(info["order_mode"], "Confirmed")
		self.assertEqual(info["order_url"], "https://test-shop.myshopify.com/admin/orders/5550001")
		self.assertIsNone(info["draft_url"])
		self.assertEqual(info["store_url"], info["order_url"])

		frappe.db.set_value("Field Order", self.order.name, "external_draft_id", "777")
		info = get_order_sync_info(self.order.name)
		self.assertEqual(info["draft_url"], "https://test-shop.myshopify.com/admin/draft_orders/777")
		self.assertEqual(info["store_url"], info["order_url"])

	# -- draft numbers ---------------------------------------------------------

	def test_draft_result_carries_the_draft_number(self):
		from field_sales.connectors.shopify import _order_result

		result = _order_result({"id": "gid://shopify/DraftOrder/777", "name": "#D5", "order": None})
		self.assertEqual(result["draft_id"], "777")
		self.assertEqual(result["draft_name"], "#D5")

	def test_backfill_fills_missing_draft_numbers(self):
		from field_sales.patches.v1_0 import backfill_store_draft_names

		frappe.db.set_value("Field Order", self.order.name, {"external_draft_id": "777", "store_draft_name": None})

		def graphql(conn, query, variables=None, operation="Other", ref=None):
			return {"nodes": [{"id": i, "name": "#D5"} for i in variables["ids"] if i.endswith("/777")]}

		with patch.object(ShopifyConnector, "_graphql", autospec=True, side_effect=graphql):
			backfill_store_draft_names.execute()
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "store_draft_name"), "#D5")
