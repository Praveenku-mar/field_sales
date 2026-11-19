# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Store orders made from Field Orders:

- the customer and shipping address carry a person's name, or Shopify's checkout refuses the
  address ("Selected shipping address is not valid");
- the order's stock is committed at the location the rep sold from (``assign_order_location``),
  not wherever Shopify's own routing put it, both for a confirmed order and a completed draft.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

from field_sales.connectors import get_connector
from field_sales.connectors.base import ConnectorError
from field_sales.connectors.shopify import (
	FULFILLMENT_ORDER_MOVE,
	ShopifyConnector,
	_customer_address,
	_customer_input,
)
from field_sales.sync import orders as sync_orders

SHOP = "test-shop.myshopify.com"
COIMBATORE = "90453311628"
SHOP_LOCATION = "90210566284"


def fulfillment_order(fo_id, location_id, actions=("MOVE",)):
	return {
		"id": f"gid://shopify/FulfillmentOrder/{fo_id}",
		"status": "OPEN",
		"supportedActions": [{"action": a} for a in actions],
		"assignedLocation": {"location": {"id": f"gid://shopify/Location/{location_id}"}},
	}


class TestStoreOrderName(IntegrationTestCase):
	def retailer(self, **values):
		return frappe._dict(
			{
				"retailer_name": "Annai Medicals",
				"owner_name": None,
				"address_line1": "12, Main Road",
				"address_line2": None,
				"city": "Madurai",
				"pincode": "625001",
				"state": None,
				"country": None,
				"email": None,
				"phone": None,
				"retailer_type": None,
				"name": "RET-NAME",
				**values,
			}
		)

	def test_address_and_customer_carry_the_owners_name(self):
		retailer = self.retailer(owner_name="Selvi  P")
		address = _customer_address(retailer, None)
		self.assertEqual((address["firstName"], address["lastName"]), ("Selvi", "P"))
		self.assertEqual(address["company"], "Annai Medicals")
		customer = _customer_input(retailer)
		self.assertEqual((customer["firstName"], customer["lastName"]), ("Selvi", "P"))

	def test_without_an_owner_the_shop_name_is_used(self):
		address = _customer_address(self.retailer(), None)
		self.assertEqual((address["firstName"], address["lastName"]), ("Annai", "Medicals"))

	def test_a_single_word_becomes_the_last_name(self):
		# Shopify's checkout needs a last name; a first name is optional.
		address = _customer_address(self.retailer(owner_name="Selvi"), None)
		self.assertEqual((address["firstName"], address["lastName"]), ("", "Selvi"))


class TestAssignOrderLocation(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": "Shopify", "enabled": 1, "store_url": SHOP, "shopify_api_version": "2026-10"},
		)
		self.conn = get_connector()

	def tearDown(self):
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def test_moves_only_groups_routed_elsewhere_that_can_move(self):
		calls = []

		def graphql(self_, query, variables=None, **kwargs):
			calls.append((query, variables))
			if query == FULFILLMENT_ORDER_MOVE:
				return {"fulfillmentOrderMove": {"movedFulfillmentOrder": {"id": variables["id"]}}}
			return {
				"order": {
					"fulfillmentOrders": {
						"nodes": [
							fulfillment_order(1, SHOP_LOCATION),
							fulfillment_order(2, COIMBATORE),  # already there
							fulfillment_order(3, SHOP_LOCATION, actions=("HOLD",)),  # cannot move
						]
					}
				}
			}

		with patch.object(ShopifyConnector, "_graphql", graphql):
			moved = self.conn.assign_order_location("6249815736460", COIMBATORE)

		self.assertEqual(moved, 1)
		self.assertEqual(calls[0][1], {"id": "gid://shopify/Order/6249815736460"})
		moves = [v for q, v in calls if q == FULFILLMENT_ORDER_MOVE]
		self.assertEqual(
			moves,
			[{"id": "gid://shopify/FulfillmentOrder/1", "loc": f"gid://shopify/Location/{COIMBATORE}"}],
		)

	def test_no_location_means_no_store_call(self):
		with patch.object(ShopifyConnector, "_graphql") as graphql:
			self.assertEqual(self.conn.assign_order_location("6249815736460", None), 0)
		graphql.assert_not_called()


class TestSyncAssignsLocation(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 1, "platform": "Shopify"})
		user = frappe.get_doc(
			{"doctype": "User", "email": "location.rep@example.com", "first_name": "Location", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": user.name}).insert(ignore_permissions=True).name
		self.retailer = (
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": "Location Stores",
					"phone": "9876543210",
					"address_line1": "1 Main Road",
					"city": "Coimbatore",
					"sales_rep": self.rep,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Location product",
				"external_product_id": "loc-1",
				"external_variant_id": "loc-1-v",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def submitted_order(self, **values):
		name = (
			frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": self.retailer,
					"sales_rep": self.rep,
					"order_date": nowdate(),
					"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value(
			"Field Order",
			name,
			{"docstatus": 1, "location_id": COIMBATORE, "location_name": "Coimbatore", **values},
			update_modified=False,
		)
		return name

	def connector(self):
		conn = MagicMock()
		conn.settings.platform = "Shopify"
		conn.settings.order_mode = "Confirmed"
		conn.settings.default_location_id = SHOP_LOCATION
		conn.settings.default_location_name = "Shop location"
		return conn

	def push(self, name, conn):
		with (
			patch.object(sync_orders, "get_connector", return_value=conn),
			patch.object(sync_orders, "_ensure_store_customer"),
			patch.object(sync_orders, "enqueue_create_entries"),
			patch.object(sync_orders, "enqueue_order_confirmation"),
			patch.object(sync_orders, "notify_order_synced"),
			patch.object(sync_orders, "_notify"),
			patch.object(sync_orders.frappe.db, "commit"),
		):
			sync_orders.push_order(name)

	def test_confirmed_order_is_moved_to_the_reps_location(self):
		name = self.submitted_order(sync_status="Pending")
		conn = self.connector()
		conn.create_order.return_value = {"order_id": "7001", "order_name": "#1001", "draft_id": None}

		self.push(name, conn)

		self.assertEqual(frappe.db.get_value("Field Order", name, "sync_status"), "Synced")
		args = conn.assign_order_location.call_args.args
		self.assertEqual((args[0], args[1], args[2].name), ("7001", COIMBATORE, name))

	def test_a_refused_move_does_not_fail_the_sync(self):
		name = self.submitted_order(sync_status="Pending")
		conn = self.connector()
		conn.create_order.return_value = {"order_id": "7002", "order_name": "#1002", "draft_id": None}
		conn.assign_order_location.side_effect = ConnectorError("Access denied", code="ACCESS_DENIED")

		self.push(name, conn)

		self.assertEqual(frappe.db.get_value("Field Order", name, "sync_status"), "Synced")
		self.assertEqual(frappe.db.get_value("Field Order", name, "external_order_id"), "7002")

	def test_a_draft_left_open_is_not_moved(self):
		name = self.submitted_order(sync_status="Pending")
		conn = self.connector()
		conn.settings.order_mode = "Draft"
		conn.create_order.return_value = {"draft_id": "D-1", "order_id": None, "order_name": None}

		self.push(name, conn)

		conn.assign_order_location.assert_not_called()

	def test_completed_draft_is_moved_when_linked(self):
		# Only this test's draft is open: older test data must not join the run.
		frappe.db.set_value(
			"Field Order",
			{"external_draft_id": ("is", "set")},
			"last_sync_attempt",
			add_to_date(now_datetime(), days=-400),
		)
		name = self.submitted_order(
			sync_status="Synced", external_draft_id="D-LOC", last_sync_attempt=now_datetime()
		)
		conn = self.connector()
		conn.get_completed_drafts.return_value = {"D-LOC": "7003"}

		with (
			patch.object(sync_orders, "get_connector", return_value=conn),
			patch.object(sync_orders, "_carry_draft_payment"),
			patch.object(sync_orders, "enqueue_create_entries"),
			patch.object(sync_orders, "enqueue_order_confirmation"),
			patch.object(sync_orders, "notify_order_synced"),
			patch.object(sync_orders, "notify_sync_result"),
			patch.object(sync_orders.frappe.db, "commit"),
		):
			sync_orders.link_completed_drafts()

		args = conn.assign_order_location.call_args.args
		self.assertEqual((args[0], args[1], args[2].name), ("7003", COIMBATORE, name))
