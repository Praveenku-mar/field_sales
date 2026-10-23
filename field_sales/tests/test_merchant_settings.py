# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Merchant settings from inside the Shopify admin: who may change what, and first-install setup."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.shopify import merchant_settings as ms

SHOP = "merchant-shop.myshopify.com"


class TestMerchantSettings(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": SHOP,
				"shopify_auth_mode": "OAuth",
				"default_location_id": None,
				"default_location_name": None,
				"order_mode": "Draft",
				"match_shop_settings": 0,
			},
		)
		settings = frappe.get_single("Store Connection")
		settings.set("locations", [])
		settings.save(ignore_permissions=True)
		self.manager = self.user("merchant.manager@example.com", "Sales Manager")
		frappe.local.shopify_shop = None
		enqueue = patch.object(ms.frappe, "enqueue")
		self.enqueue = enqueue.start()
		self.addCleanup(enqueue.stop)

	def tearDown(self):
		frappe.local.shopify_shop = None
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def user(self, email, role):
		if not frappe.db.exists("User", email):
			doc = frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "M", "send_welcome_email": 0}
			)
			doc.insert(ignore_permissions=True)
			doc.add_roles(role)
		return email

	def as_embedded_manager(self, shop=SHOP):
		frappe.set_user(self.manager)
		frappe.local.shopify_shop = shop

	def test_manager_needs_the_shops_session(self):
		frappe.set_user(self.manager)  # signed in by cookie, not from the Shopify admin
		with self.assertRaises(frappe.PermissionError):
			ms.get_store_settings()
		self.as_embedded_manager(shop="other-shop.myshopify.com")
		with self.assertRaises(frappe.PermissionError):
			ms.get_store_settings()
		self.as_embedded_manager()
		self.assertEqual(ms.get_store_settings()["store_url"], SHOP)

	def test_rep_from_the_admin_is_refused(self):
		rep = self.user("merchant.rep@example.com", "Field Sales Rep")
		frappe.set_user(rep)
		frappe.local.shopify_shop = SHOP
		with self.assertRaises(frappe.PermissionError):
			ms.get_store_settings()

	def test_settings_never_expose_credentials(self):
		self.as_embedded_manager()
		values = ms.get_store_settings()
		for field in (
			"shopify_client_id",
			"shopify_client_secret",
			"access_token",
			"refresh_token",
			"embedded_user",
		):
			self.assertNotIn(field, values)

	def test_saves_only_merchant_fields_and_queues_sync(self):
		self.as_embedded_manager()
		ms.save_store_settings(
			{
				"order_mode": "Confirmed",
				"company_name": "Merchant Co",
				"default_location_id": "100",
				"default_location_name": "Main",
				"locations": [
					{"location_id": "100", "location_name": "Main", "sell_from": 1},
					{"location_id": "200", "sell_from": 0},
				],
				# Not editable here:
				"store_url": "evil.myshopify.com",
				"shopify_auth_mode": "Client Credentials",
				"enabled": 0,
				"embedded_user": "Administrator",
			}
		)
		settings = frappe.get_single("Store Connection")
		self.assertEqual(
			(settings.order_mode, settings.company_name, settings.default_location_id),
			("Confirmed", "Merchant Co", "100"),
		)
		self.assertEqual([r.location_id for r in settings.locations], ["100", "200"])
		self.assertEqual(
			(settings.store_url, settings.shopify_auth_mode, settings.enabled), (SHOP, "OAuth", 1)
		)
		self.assertNotEqual(settings.embedded_user, "Administrator")
		self.assertEqual(self.enqueue.call_args.args[0], "field_sales.sync.products.sync_products")

	def test_setup_new_store_picks_first_location_once(self):
		with (
			patch(
				"field_sales.connectors.shopify.ShopifyConnector.get_locations",
				return_value=[{"id": "7", "name": "Warehouse"}, {"id": "8", "name": "Shop"}],
			),
			patch("field_sales.sync.products.frappe.enqueue") as sync,
			patch.object(frappe.db, "commit"),
		):
			ms.setup_new_store()
			settings = frappe.get_single("Store Connection")
			self.assertEqual(
				(settings.default_location_id, settings.default_location_name), ("7", "Warehouse")
			)
			self.assertEqual([(r.location_id, r.sell_from) for r in settings.locations], [("7", 1)])
			sync.assert_called_once()

			frappe.db.set_single_value("Store Connection", "default_location_id", "8")
			ms.setup_new_store()  # a choice is never overridden
			self.assertEqual(frappe.db.get_single_value("Store Connection", "default_location_id"), "8")

	def test_email_accounts_need_the_shops_session(self):
		frappe.set_user(self.manager)
		with self.assertRaises(frappe.PermissionError):
			ms.get_email_accounts()
		self.as_embedded_manager()
		self.assertIsInstance(ms.get_email_accounts(), list)
