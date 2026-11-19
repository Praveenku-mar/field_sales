# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
A draft completed on the store (its checkout, or the admin) is linked to its Field Order as soon as
an order event for it arrives (``sync.orders.link_order_from_event``), not only by the 30-minute
draft linker; the store must confirm that the Field Order's own draft became that order.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime, nowdate

from field_sales.connectors import get_connector
from field_sales.sync import orders as sync_orders
from field_sales.sync import webhooks as sync_webhooks

SHOP = "test-shop.myshopify.com"


class TestOrderNoteHint(IntegrationTestCase):
	def setUp(self):
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": "Shopify", "enabled": 1, "store_url": SHOP, "shopify_api_version": "2026-10"},
		)

	def tearDown(self):
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def test_reads_the_field_order_no_from_the_drafts_note(self):
		conn = get_connector()
		note = "Field Order FO-2026-00089 · Rep rep@example.com · Location Coimbatore"
		self.assertEqual(conn.field_order_from_order_event({"note": note}), "FO-2026-00089")
		self.assertIsNone(conn.field_order_from_order_event({"note": "Gift wrap please"}))
		self.assertIsNone(conn.field_order_from_order_event({"note": None}))
		self.assertIsNone(conn.field_order_from_order_event({}))


class TestOrderEventLink(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 1, "platform": "Shopify"})
		user = frappe.get_doc(
			{"doctype": "User", "email": "eventlink.rep@example.com", "first_name": "Event", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		rep = frappe.get_doc({"doctype": "Sales Rep", "user": user.name}).insert(ignore_permissions=True).name
		retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Event Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Madurai",
				"sales_rep": rep,
			}
		).insert(ignore_permissions=True)
		product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Event product",
				"external_product_id": "ev-1",
				"external_variant_id": "ev-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.name = (
			frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": retailer.name,
					"sales_rep": rep,
					"order_date": nowdate(),
					"items": [{"product": product.name, "qty": 1, "rate": 100}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value(
			"Field Order",
			self.name,
			{
				"docstatus": 1,
				"sync_status": "Synced",
				"external_draft_id": "D-EV",
				"external_order_id": None,
				"last_sync_attempt": now_datetime(),
			},
			update_modified=False,
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def connector(self, completed):
		conn = MagicMock()
		conn.settings.platform = "Shopify"
		conn.field_order_from_order_event.return_value = self.name
		conn.get_completed_drafts.return_value = completed
		return conn

	def side_effects(self):
		self.confirmations = []
		return (
			patch.object(sync_orders, "_carry_draft_payment"),
			patch.object(sync_orders, "enqueue_create_entries"),
			patch.object(sync_orders, "enqueue_order_confirmation", side_effect=self.confirmations.append),
			patch.object(sync_orders, "notify_order_synced"),
			patch.object(sync_orders, "notify_sync_result"),
		)

	def link(self, conn, order_id="8001"):
		patches = self.side_effects()
		with patches[0], patches[1], patches[2], patches[3], patches[4]:
			return sync_orders.link_order_from_event(conn, order_id, {"note": "…"})

	def test_paid_event_links_the_completed_draft_at_once(self):
		conn = self.connector({"D-EV": "8001"})

		self.assertEqual(self.link(conn), self.name)

		self.assertEqual(frappe.db.get_value("Field Order", self.name, "external_order_id"), "8001")
		conn.get_completed_drafts.assert_called_once_with(["D-EV"])
		self.assertEqual(self.confirmations, [self.name])
		args = conn.assign_order_location.call_args
		self.assertIsNone(args)  # the test order has no location

	def test_a_note_naming_another_order_is_not_trusted(self):
		# The store says this Field Order's draft became a different order (or is still open).
		for completed in ({"D-EV": "9999"}, {}):
			conn = self.connector(completed)
			self.assertIsNone(self.link(conn))
		self.assertIsNone(frappe.db.get_value("Field Order", self.name, "external_order_id"))
		self.assertEqual(self.confirmations, [])

	def test_a_known_order_needs_no_store_call(self):
		frappe.db.set_value("Field Order", self.name, "external_order_id", "8001", update_modified=False)
		conn = self.connector({"D-EV": "8001"})

		self.assertIsNone(self.link(conn))
		conn.field_order_from_order_event.assert_not_called()
		conn.get_completed_drafts.assert_not_called()

	def test_the_draft_linker_does_not_link_it_again(self):
		conn = self.connector({"D-EV": "8001"})
		self.link(conn)

		patches = self.side_effects()
		with (
			patch.object(sync_orders, "get_connector", return_value=conn),
			patch.object(sync_orders.frappe.db, "commit"),
			patches[0],
			patches[1],
			patches[2],
			patches[3],
			patches[4],
		):
			sync_orders.link_completed_drafts()

		self.assertEqual(self.confirmations, [])  # sent once, by the webhook

	def test_order_webhook_links_before_reconciling(self):
		conn = self.connector({"D-EV": "8001"})
		conn.order_id_from_event.return_value = "8001"
		seen = {}

		def reconcile(order_id):
			seen["linked"] = frappe.db.get_value("Field Order", {"external_order_id": order_id}, "name")
			return {"created": 0}

		patches = self.side_effects()
		with (
			patch.object(sync_webhooks, "reconcile_order", side_effect=reconcile),
			patch("field_sales.payments.store_payments.apply_store_payment", return_value={"note": "unpaid"}),
			patches[0],
			patches[1],
			patches[2],
			patches[3],
			patches[4],
		):
			result = sync_webhooks._process_order_event(conn, {"payload": {"id": 8001, "note": "…"}})

		self.assertEqual(seen["linked"], self.name)
		self.assertEqual(result["linked"], self.name)


class TestOrderCreatedTopic(IntegrationTestCase):
	def setUp(self):
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": "Shopify", "enabled": 1, "store_url": SHOP, "shopify_api_version": "2026-10"},
		)

	def tearDown(self):
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def test_orders_create_is_an_order_event(self):
		# A manual or cash payment at checkout sends "created" but never "paid": the draft must still link.
		from field_sales.connectors.base import ORDER_CREATED_TOPIC, ORDER_TOPICS

		event = get_connector().parse_webhook({"X-Shopify-Topic": "orders/create"}, b'{"id": 8001}')
		self.assertEqual(event["topic"], ORDER_CREATED_TOPIC)
		self.assertIn(ORDER_CREATED_TOPIC, ORDER_TOPICS)

		with patch.object(sync_webhooks, "_process_order_event", return_value={"note": "ok"}) as handled:
			sync_webhooks.process_event("Shopify", event)
		handled.assert_called_once()


class TestWebhookJobUser(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def test_a_store_event_is_processed_as_the_system_not_guest(self):
		# The delivery is accepted as Guest; jobs it queues (the order confirmation prints the
		# invoice) run as the user who queued them, and Guest may not print a Field Order.
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": "Shopify", "enabled": 1, "store_url": SHOP, "shopify_api_version": "2026-10"},
		)
		seen = {}

		def handle(conn, event):
			seen["user"] = frappe.session.user
			seen["can_print"] = frappe.has_permission("Field Order", "print")
			return {"note": "ok"}

		frappe.set_user("Guest")
		with patch.object(sync_webhooks, "_process_order_event", side_effect=handle):
			sync_webhooks.process_event("Shopify", {"topic": "order/paid", "payload": {"id": 1}})

		self.assertEqual(seen["user"], "Administrator")
		self.assertTrue(seen["can_print"])
