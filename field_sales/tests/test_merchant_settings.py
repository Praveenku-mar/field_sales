# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Merchant settings from inside the Shopify admin: who may change what, and first-install setup."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.shopify import merchant_settings as ms
from field_sales.utils.store import get_store, get_store_value, set_store_value

SHOP = "merchant-shop.myshopify.com"


class TestMerchantSettings(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_store_value(
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": SHOP,
				"shopify_auth_mode": "OAuth",
				"default_location_id": None,
				"default_location_name": None,
				"order_mode": "Draft",
				"match_shop_settings": 0,
				# A site may already use Administrator here; the save test checks it is not set.
				"embedded_user": None,
			}
		)
		settings = get_store()
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
		settings = get_store()
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
			settings = get_store()
			self.assertEqual(
				(settings.default_location_id, settings.default_location_name), ("7", "Warehouse")
			)
			self.assertEqual([(r.location_id, r.sell_from) for r in settings.locations], [("7", 1)])
			sync.assert_called_once()

			set_store_value("default_location_id", "8")
			ms.setup_new_store()  # a choice is never overridden
			self.assertEqual(get_store_value("default_location_id"), "8")

	def test_email_accounts_need_the_shops_session(self):
		frappe.set_user(self.manager)
		with self.assertRaises(frappe.PermissionError):
			ms.get_email_accounts()
		self.as_embedded_manager()
		self.assertIsInstance(ms.get_email_accounts(), list)


class TestMerchantEmailAccounts(IntegrationTestCase):
	"""
	The Email tab from the Shopify admin: a merchant changes only accounts added there, and never
	re-points a saved account to another server without its password (Frappe would sign in to the
	new server with the stored one). The SMTP sign-in check itself is skipped in tests.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		set_store_value(
			{"platform": "Shopify", "enabled": 1, "store_url": SHOP, "shopify_auth_mode": "OAuth"},
		)
		frappe.db.set_default(ms.MERCHANT_EMAIL_ACCOUNTS_KEY, "[]")
		self.manager = TestMerchantSettings.user(self, "merchant.mail@example.com", "Sales Manager")
		# The site's own account, set up by a System Manager.
		self.site_account = self.account("site.mail@example.com", "smtp.site-host.example")
		frappe.local.shopify_shop = None

	def tearDown(self):
		frappe.local.shopify_shop = None
		frappe.set_user("Administrator")
		frappe.db.rollback()
		# Site defaults are cached: drop what the rolled-back writes left there.
		frappe.defaults._clear_cache("__default")

	def account(self, email, server):
		return (
			frappe.get_doc(
				{
					"doctype": "Email Account",
					"email_id": email,
					"auth_method": "Basic",
					"enable_incoming": 0,
					"enable_outgoing": 1,
					"smtp_server": server,
					"smtp_port": "587",
					"use_tls": 1,
					"password": "site-secret",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def as_merchant(self):
		frappe.set_user(self.manager)
		frappe.local.shopify_shop = SHOP

	def merchant_account(self):
		self.as_merchant()
		return ms.save_email_account(
			{"email_id": "shop.mail@example.com", "smtp_server": "smtp.shop-host.example", "smtp_port": "587",
			 "use_tls": 1, "password": "shop-secret"}
		)["name"]  # fmt: skip

	def test_merchant_cannot_edit_the_sites_own_account(self):
		self.as_merchant()
		with self.assertRaises(frappe.PermissionError):
			ms.save_email_account(
				{"smtp_server": "smtp.attacker.example", "password": "x"}, name=self.site_account
			)
		self.assertEqual(
			frappe.db.get_value("Email Account", self.site_account, "smtp_server"), "smtp.site-host.example"
		)

	def test_merchant_lists_only_accounts_added_from_the_tab(self):
		mine = self.merchant_account()
		names = {a.name for a in ms.get_email_accounts()}
		self.assertIn(mine, names)
		self.assertNotIn(self.site_account, names)

	def test_merchant_edits_own_account(self):
		mine = self.merchant_account()
		ms.save_email_account({"always_use_account_email_id_as_sender": 1, "default_outgoing": 1}, name=mine)
		self.assertEqual(frappe.db.get_value("Email Account", mine, "default_outgoing"), 1)

	def test_changing_the_server_needs_the_password(self):
		mine = self.merchant_account()
		for change in ({"smtp_server": "smtp.elsewhere.example"}, {"smtp_port": "2525"}, {"use_tls": 0},
				{"login_id_is_different": 1, "login_id": "someone"}):  # fmt: skip
			with self.subTest(change=change), self.assertRaises(frappe.ValidationError):
				ms.save_email_account(change, name=mine)
		self.assertEqual(frappe.db.get_value("Email Account", mine, "smtp_server"), "smtp.shop-host.example")
		ms.save_email_account({"smtp_server": "smtp.elsewhere.example", "password": "new-secret"}, name=mine)
		self.assertEqual(frappe.db.get_value("Email Account", mine, "smtp_server"), "smtp.elsewhere.example")

	def test_unchanged_connection_needs_no_password(self):
		mine = self.merchant_account()
		ms.save_email_account(
			{
				"smtp_server": "SMTP.shop-host.example ",
				"smtp_port": "587",
				"use_tls": 1,
				"enable_outgoing": 0,
			},
			name=mine,
		)
		self.assertEqual(frappe.db.get_value("Email Account", mine, "enable_outgoing"), 0)

	def test_system_manager_sees_and_edits_every_account(self):
		self.assertIn(self.site_account, {a.name for a in ms.get_email_accounts()})
		with self.assertRaises(frappe.ValidationError):
			ms.save_email_account({"smtp_server": "smtp.new-host.example"}, name=self.site_account)
		ms.save_email_account(
			{"smtp_server": "smtp.new-host.example", "password": "s"}, name=self.site_account
		)
		self.assertEqual(
			frappe.db.get_value("Email Account", self.site_account, "smtp_server"), "smtp.new-host.example"
		)
