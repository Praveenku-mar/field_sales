# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.connectors.base import ConnectorError
from field_sales.connectors.shopify import SHOP_INFO_QUERY, TEST_CONNECTION_QUERY, ShopifyConnector
from field_sales.sync import shop_settings
from field_sales.sync.shop_settings import get_mismatch, scheduled_sync, sync_shop_settings


class TestShopSettings(IntegrationTestCase):
	"""The site follows the store's currency and time zone."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": "test-shop.myshopify.com",
				"shopify_api_version": "2026-10",
				"match_shop_settings": 1,
				"shop_currency": None,
				"shop_timezone": None,
			},
		)
		for code in ("INR", "USD"):
			if not frappe.db.exists("Currency", code):
				frappe.get_doc({"doctype": "Currency", "currency_name": code, "enabled": 1}).insert()
		frappe.db.set_value("Currency", "USD", "enabled", 0)
		self.set_site("INR", "Asia/Kolkata")

	def tearDown(self):
		frappe.db.rollback()
		# Defaults and the time zone are cached; the rollback doesn't clear them.
		frappe.clear_cache()

	def set_site(self, currency, tz):
		frappe.db.set_single_value("System Settings", {"currency": currency, "time_zone": tz})
		frappe.db.set_default("currency", currency)
		frappe.db.set_default("time_zone", tz)
		frappe.cache.delete_value("time_zone")

	def store(self, **shop):
		shop = {"name": "Test", "myshopifyDomain": "test-shop.myshopify.com", **shop}

		def graphql(conn, query, variables=None, operation="Other", ref=None):
			if query in (SHOP_INFO_QUERY, TEST_CONNECTION_QUERY):
				return {"shop": shop, "currentAppInstallation": {"accessScopes": []}}
			raise AssertionError("unexpected query")

		return patch.object(ShopifyConnector, "_graphql", autospec=True, side_effect=graphql)

	# -- applying ----------------------------------------------------------------

	def test_applies_store_currency_and_timezone(self):
		with self.store(currencyCode="USD", ianaTimezone="America/New_York"):
			result = sync_shop_settings()

		self.assertEqual(len(result["changes"]), 2)
		self.assertEqual(result["mismatch"], [])
		self.assertEqual(frappe.db.get_default("currency"), "USD")
		self.assertEqual(frappe.db.get_single_value("System Settings", "currency"), "USD")
		self.assertEqual(frappe.db.get_single_value("System Settings", "time_zone"), "America/New_York")
		self.assertEqual(frappe.db.get_default("time_zone"), "America/New_York")
		self.assertEqual(frappe.db.get_value("Currency", "USD", "enabled"), 1)
		saved = frappe.db.get_value(
			"Store Connection", None, ["shop_currency", "shop_timezone"], as_dict=True
		)
		self.assertEqual((saved.shop_currency, saved.shop_timezone), ("USD", "America/New_York"))

	def test_same_settings_change_nothing(self):
		with (
			self.store(currencyCode="INR", ianaTimezone="Asia/Kolkata"),
			patch.object(frappe, "clear_cache") as clear,
		):
			result = sync_shop_settings()
		self.assertEqual(result, {"changes": [], "mismatch": []})
		clear.assert_not_called()

	def test_match_off_only_saves_and_reports(self):
		frappe.db.set_single_value("Store Connection", "match_shop_settings", 0)
		with self.store(currencyCode="USD", ianaTimezone="America/New_York"):
			result = sync_shop_settings()
		self.assertEqual(result["changes"], [])
		self.assertEqual(len(result["mismatch"]), 2)
		self.assertEqual(frappe.db.get_default("currency"), "INR")
		self.assertEqual(frappe.db.get_single_value("Store Connection", "shop_currency"), "USD")

	def test_unknown_currency_and_timezone_are_skipped(self):
		with (
			self.store(currencyCode="XYZ", ianaTimezone="Mars/Olympus"),
			patch.object(frappe, "log_error") as log,
		):
			result = sync_shop_settings()
		self.assertEqual(result["changes"], [])
		self.assertEqual(log.call_count, 2)
		self.assertEqual(frappe.db.get_default("currency"), "INR")
		self.assertEqual(frappe.db.get_single_value("System Settings", "time_zone"), "Asia/Kolkata")

	def test_get_mismatch_uses_saved_store_values(self):
		frappe.db.set_single_value(
			"Store Connection", {"shop_currency": "USD", "shop_timezone": "Asia/Kolkata"}
		)
		(line,) = get_mismatch()
		self.assertIn("USD", line)

	# -- scheduled job -----------------------------------------------------------

	def test_scheduled_sync_notifies_the_user(self):
		with (
			self.store(currencyCode="USD", ianaTimezone="Asia/Kolkata"),
			patch.object(frappe, "publish_realtime") as publish,
		):
			scheduled_sync(notify_user="Administrator")
		(args, kwargs) = publish.call_args
		self.assertEqual(args[0], "msgprint")
		self.assertIn("USD", args[1]["message"])
		self.assertEqual(kwargs["user"], "Administrator")

	def test_scheduled_sync_skips_when_disabled_or_off(self):
		for values in ({"enabled": 0}, {"match_shop_settings": 0}):
			with self.subTest(values):
				frappe.db.set_single_value(
					"Store Connection", {"enabled": 1, "match_shop_settings": 1, **values}
				)
				with patch.object(shop_settings, "sync_shop_settings") as sync:
					scheduled_sync()
				sync.assert_not_called()

	def test_scheduled_sync_logs_store_errors(self):
		with (
			patch.object(
				ShopifyConnector, "_graphql", side_effect=ConnectorError("down", code="NETWORK_ERROR")
			),
			patch.object(frappe, "log_error") as log,
			patch.object(frappe, "publish_realtime") as publish,
		):
			scheduled_sync(notify_user="Administrator")
		log.assert_called_once()
		publish.assert_not_called()

	# -- Store Connection save ---------------------------------------------------

	def test_saving_the_connection_queues_a_background_sync(self):
		doc = frappe.get_single("Store Connection")
		with patch.object(frappe, "enqueue") as enqueue:
			doc.save()
		self.assertEqual(enqueue.call_args.args[0], "field_sales.sync.shop_settings.scheduled_sync")
		self.assertEqual(enqueue.call_args.kwargs["notify_user"], "Administrator")
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

		# Nothing relevant changed and the store's values are known: no job.
		frappe.db.set_single_value("Store Connection", "shop_currency", "INR")
		doc = frappe.get_single("Store Connection")
		doc.default_location_name = "Changed"
		with patch.object(frappe, "enqueue") as enqueue:
			doc.save()
		enqueue.assert_not_called()

	# -- connector ---------------------------------------------------------------

	def test_test_connection_returns_currency_and_timezone(self):
		with self.store(currencyCode="USD", ianaTimezone="America/New_York"):
			result = frappe.get_attr("field_sales.connectors.get_connector")().test_connection()
		self.assertEqual((result["currency"], result["timezone"]), ("USD", "America/New_York"))

		with patch.object(ShopifyConnector, "_graphql", side_effect=ConnectorError("down")):
			result = frappe.get_attr("field_sales.connectors.get_connector")().test_connection()
		self.assertEqual((result["ok"], result["currency"], result["timezone"]), (False, "", ""))

	def test_order_in_wrong_currency_is_refused(self):
		conn = frappe.get_attr("field_sales.connectors.get_connector")()
		with self.store(currencyCode="USD", ianaTimezone="Asia/Kolkata"):
			with self.assertRaises(ConnectorError) as ctx:
				conn._get_currency("FO-TEST")
		self.assertEqual(ctx.exception.code, "CURRENCY_MISMATCH")

		frappe.db.set_default("currency", "USD")
		self.assertEqual(conn._get_currency("FO-TEST"), "USD")
