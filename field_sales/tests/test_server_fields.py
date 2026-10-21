# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from field_sales.sync.customers import push_retailer
from field_sales.sync.orders import push_order

SHOP = "test-shop.myshopify.com"


# What a payment webhook would record; clients must never be able to set it.
PAID = {
	"payment_status": "Paid",
	"amount_paid": 100,
	"razorpay_payment_link_id": "plink_X",
	"razorpay_payment_link_url": "https://rzp.io/i/x",
	"razorpay_payment_id": "pay_X",
	"paid_on": "2026-10-21 10:00:00",
}


class TestServerFields(IntegrationTestCase):
	"""Store ids and sync state are written by the sync only, never through a client save."""

	def setUp(self):
		frappe.set_user("Administrator")
		# set_single_value does not validate; everything rolls back after each test.
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": "Shopify", "enabled": 0, "store_url": SHOP, "default_location_id": "", "order_mode": "Draft"},
		)
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Server fields product",
				"external_product_id": "9300",
				"external_variant_id": "93",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		self.rep_user = self.make_user("serverfields.rep@example.com", ["Field Sales Rep"])
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": self.rep_user}).insert(ignore_permissions=True)
		self.sysman = self.make_user("serverfields.sysman@example.com", ["System Manager"])
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Server Fields Stores",
				"phone": "9876543210",
				"address_line1": "5 Market Street",
				"city": "Salem",
				"sales_rep": self.rep.name,
			}
		).insert(ignore_permissions=True)
		self.order = self.as_user(self.rep_user, self.new_order().insert)

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

	def new_order(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer.name,
				"sales_rep": self.rep.name,
				"order_date": nowdate(),
				"items": [{"product": self.product.name, "qty": 1, "rate": 100}],
				**values,
			}
		)

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def order_value(self, fieldname, name=None):
		return frappe.db.get_value("Field Order", name or self.order.name, fieldname)

	def enable_store(self):
		frappe.db.set_single_value("Store Connection", "enabled", 1)

	# -- Field Order -------------------------------------------------------

	def test_rep_cannot_set_draft_id(self):
		self.as_user(self.rep_user, frappe.client.set_value, "Field Order", self.order.name, "external_draft_id", "999")
		self.assertFalse(self.order_value("external_draft_id"))

	def test_rep_cannot_set_sync_state_on_save(self):
		doc = frappe.get_doc("Field Order", self.order.name).as_dict()
		doc.update(external_order_id="555", sync_status="Synced", platform="Shopify", sync_attempts=3)
		self.as_user(self.rep_user, frappe.client.save, doc)
		values = frappe.db.get_value(
			"Field Order", self.order.name, ["external_order_id", "sync_status", "platform", "sync_attempts"], as_dict=True
		)
		self.assertEqual((values.external_order_id, values.sync_status, values.platform, values.sync_attempts), (None, "Draft", None, 0))

	def test_new_order_with_store_ids_starts_unsynced(self):
		order = self.as_user(
			self.rep_user, self.new_order(external_draft_id="999", external_order_id="555", sync_status="Synced").insert
		)
		self.assertEqual(
			(self.order_value("external_draft_id", order.name), self.order_value("external_order_id", order.name)),
			(None, None),
		)
		self.assertEqual(self.order_value("sync_status", order.name), "Draft")

	def test_location_pick_is_kept(self):
		# location_id is the user's choice (validated against the sell locations), not a sync field.
		doc = frappe.get_doc("Field Order", self.order.name).as_dict()
		doc["location_id"] = "LOC-ANY"
		self.as_user(self.rep_user, frappe.client.save, doc)
		self.assertEqual(self.order_value("location_id"), "LOC-ANY")

	def test_copy_of_cancelled_order_has_empty_store_ids(self):
		# Field Order has no amended_from, so a cancelled order is redone by duplicating it.
		frappe.db.set_value(
			"Field Order",
			self.order.name,
			{"docstatus": 2, "external_draft_id": "D1", "external_order_id": "O1", "sync_status": "Synced", "platform": "Shopify"},
		)
		cancelled = frappe.get_doc("Field Order", self.order.name)
		amended = frappe.copy_doc(cancelled)
		amended.docstatus = 0  # as the form's Duplicate does
		self.as_user(self.rep_user, amended.insert)
		values = frappe.db.get_value(
			"Field Order", amended.name, ["external_draft_id", "external_order_id", "sync_status", "platform"], as_dict=True
		)
		self.assertEqual(tuple(values.values()), (None, None, "Draft", None))

	# -- Field Order payment ------------------------------------------------

	def payment_values(self, name=None):
		return frappe.db.get_value("Field Order", name or self.order.name, list(PAID), as_dict=True)

	def assert_unpaid(self, name=None):
		values = self.payment_values(name)
		self.assertEqual(values.payment_status, "Unpaid")
		self.assertFalse(values.amount_paid)
		ids = ("razorpay_payment_link_id", "razorpay_payment_link_url", "razorpay_payment_id", "paid_on")
		self.assertFalse(any(values[f] for f in ids))

	def test_new_order_starts_unpaid(self):
		order = self.as_user(self.rep_user, self.new_order(**PAID).insert)
		self.assert_unpaid(order.name)

	def test_rep_cannot_set_payment_on_draft(self):
		doc = frappe.get_doc("Field Order", self.order.name).as_dict()
		doc.update(PAID)
		self.as_user(self.rep_user, frappe.client.save, doc)
		self.assert_unpaid()

	def test_nobody_can_mark_submitted_order_paid(self):
		# allow_on_submit fields can be changed by "Update"; validate() does not run there.
		frappe.db.set_value("Field Order", self.order.name, {"docstatus": 1, "sync_status": "Synced"})
		for user in (self.rep_user, self.sysman):
			self.as_user(user, frappe.client.set_value, "Field Order", self.order.name, dict(PAID))
			self.assert_unpaid()

	def test_server_db_set_records_payment(self):
		frappe.db.set_value("Field Order", self.order.name, "docstatus", 1)
		frappe.get_doc("Field Order", self.order.name).db_set(PAID)
		self.assertEqual(self.payment_values().payment_status, "Paid")

	def test_copy_of_paid_order_starts_unpaid(self):
		frappe.db.set_value("Field Order", self.order.name, {"docstatus": 2, **PAID})
		copy = frappe.copy_doc(frappe.get_doc("Field Order", self.order.name))
		copy.docstatus = 0
		self.as_user(self.rep_user, copy.insert)
		self.assert_unpaid(copy.name)

	def test_system_manager_may_correct_saved_order(self):
		self.as_user(self.sysman, frappe.client.set_value, "Field Order", self.order.name, "external_order_id", "777")
		self.assertEqual(self.order_value("external_order_id"), "777")

	def test_order_sync_records_ids(self):
		self.enable_store()
		frappe.db.set_value("Retailer", self.retailer.name, "external_customer_id", "C1")
		self.as_user(self.rep_user, frappe.get_doc("Field Order", self.order.name).submit)
		with (
			patch(
				"field_sales.connectors.shopify.ShopifyConnector.create_order",
				return_value={"draft_id": "D42", "order_id": None, "order_name": "#D42"},
			),
			patch.object(frappe.db, "commit"),
		):
			push_order(self.order.name)
		values = frappe.db.get_value("Field Order", self.order.name, ["external_draft_id", "sync_status", "platform"], as_dict=True)
		self.assertEqual((values.external_draft_id, values.sync_status, values.platform), ("D42", "Synced", "Shopify"))

	# -- Retailer ----------------------------------------------------------

	def test_rep_cannot_set_customer_id(self):
		self.as_user(self.rep_user, frappe.client.set_value, "Retailer", self.retailer.name, "external_customer_id", "999")
		self.assertFalse(frappe.db.get_value("Retailer", self.retailer.name, "external_customer_id"))

	def test_rep_cannot_set_last_visit(self):
		self.as_user(self.rep_user, frappe.client.set_value, "Retailer", self.retailer.name, "last_visit", "2020-01-01 10:00:00")
		self.assertFalse(frappe.db.get_value("Retailer", self.retailer.name, "last_visit"))

	def test_new_retailer_with_customer_id_starts_unlinked(self):
		retailer = self.as_user(
			self.rep_user,
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": "Planted Stores",
					"phone": "9876543211",
					"address_line1": "6 Market Street",
					"city": "Salem",
					"external_customer_id": "999",
					"sync_status": "Synced",
				}
			).insert,
		)
		values = frappe.db.get_value("Retailer", retailer.name, ["external_customer_id", "sync_status"], as_dict=True)
		self.assertEqual((values.external_customer_id, values.sync_status), (None, "Draft"))

	def test_retailer_sync_records_customer_id(self):
		self.enable_store()
		with (
			patch("field_sales.connectors.shopify.ShopifyConnector.create_customer", return_value={"id": "C77"}),
			patch.object(frappe.db, "commit"),
		):
			push_retailer(self.retailer.name)
		values = frappe.db.get_value("Retailer", self.retailer.name, ["external_customer_id", "sync_status"], as_dict=True)
		self.assertEqual((values.external_customer_id, values.sync_status), ("C77", "Synced"))
