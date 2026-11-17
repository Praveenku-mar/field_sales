# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify App Store review fixes: no payment recorded on Shopify for money taken outside its
checkout (rule 1.1.2), no outside payment provider settings (1.1.7), app/uninstalled declared
in the app config, HTTPS-only URLs for Shopify (3.1.1), landing in the admin after install (2.3.3).
"""

import tomllib
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales import frontend_api
from field_sales.shopify import config
from field_sales.sync import orders


class AppStoreSite:
	def use(self, auth_mode, platform="Shopify"):
		frappe.db.set_single_value(
			"Store Connection", {"platform": platform, "shopify_auth_mode": auth_mode, "enabled": 1, "store_url": "kovai-mart.myshopify.com"}
		)
		frappe.clear_document_cache("Store Connection", "Store Connection")


class TestNoStorePaymentsOnAppStore(AppStoreSite, IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("Store Connection", "Store Connection")

	def test_app_store_install_never_marks_the_shopify_order_paid(self):
		self.use(config.OAUTH_MODE)
		with patch.object(orders, "get_connector") as get_connector, patch.object(orders.frappe, "get_doc", wraps=frappe.get_doc) as get_doc:
			orders.mark_store_order_paid("FO-ANY")
			orders.retry_store_payments()
		get_connector.assert_not_called()
		self.assertFalse(any(c.args[:1] == ("Field Order",) for c in get_doc.call_args_list))

	def test_direct_installs_still_do(self):
		self.use(config.CLIENT_CREDENTIALS_MODE)
		self.assertFalse(orders.store_payments_off())
		self.use(config.OAUTH_MODE, platform="WooCommerce")
		self.assertFalse(orders.store_payments_off())


class TestStoreInfoFlag(AppStoreSite, IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_app_store_flag(self):
		self.use(config.OAUTH_MODE)
		self.assertTrue(frontend_api._store_info()["app_store"])
		self.use(config.CLIENT_CREDENTIALS_MODE)
		self.assertFalse(frontend_api._store_info()["app_store"])


class TestHttpsOnly(IntegrationTestCase):
	def test_public_url_is_https_only(self):
		with patch.dict(frappe.local.conf, {"shopify_app_url": "https://fs.example.com/"}):
			self.assertEqual(config.public_url("/cb"), "https://fs.example.com/cb")
		with patch.dict(frappe.local.conf, {"shopify_app_url": "http://fs.example.com"}):
			self.assertRaises(frappe.ValidationError, config.public_url, "/cb")
		# No app URL: the site's own URL only when it is HTTPS.
		with patch.dict(frappe.local.conf, {"shopify_app_url": ""}), patch.object(config, "get_url", return_value="http://site.local:8000"):
			self.assertRaises(frappe.ValidationError, config.public_url, "/cb")
		with patch.dict(frappe.local.conf, {"shopify_app_url": ""}), patch.object(config, "get_url", return_value="https://sales.example.com"):
			self.assertEqual(config.public_url("/cb"), "https://sales.example.com/cb")


class TestAdminLanding(IntegrationTestCase):
	def test_embedded_app_url(self):
		with patch.dict(frappe.local.conf, {"shopify_app_client_id": "abc123"}):
			self.assertEqual(config.embedded_app_url("kovai-mart.myshopify.com"), "https://kovai-mart.myshopify.com/admin/apps/abc123")


class TestAppConfig(IntegrationTestCase):
	def test_toml_declares_uninstall_and_privacy_webhooks(self):
		toml = tomllib.loads((Path(frappe.get_app_path("field_sales")).parent / "shopify.app.toml").read_text())
		subs = toml["webhooks"]["subscriptions"]
		receive = [s for s in subs if s["uri"].endswith("field_sales.shopify.webhooks.receive")]
		self.assertIn("app/uninstalled", [t for s in receive for t in s.get("topics", [])])
		self.assertEqual(
			sorted(t for s in receive for t in s.get("compliance_topics", [])),
			["customers/data_request", "customers/redact", "shop/redact"],
		)
		self.assertTrue(all(s["uri"].startswith("https://") for s in subs))


class TestMobileStorePaymentAction(AppStoreSite, IntegrationTestCase):
	"""The order page's Shopify payment button, web and mobile (frontend_api._store_payment_action)."""

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("Store Connection", "Store Connection")

	def action(self, manager=True, retailer=False, **values):
		doc = frappe._dict(
			{"docstatus": 1, "payment_status": "Paid", "store_marked_paid_on": None, "external_order_id": "555", "external_draft_id": None, **values}
		)
		conn = frappe._dict(get_order_url=lambda order_id, draft_id=None: f"https://kovai-mart.myshopify.com/admin/{'orders/' + order_id if order_id else 'draft_orders/' + draft_id}")
		with patch.object(frontend_api, "is_manager_or_admin", return_value=manager), patch.object(
			frontend_api, "is_retailer_user", return_value=retailer
		), patch.object(frontend_api, "get_connector", return_value=conn):
			return frontend_api._store_payment_action(doc)

	def test_paid_in_field_sales_sends_the_manager_to_mark_it_paid(self):
		self.use(config.OAUTH_MODE)
		got = self.action()
		self.assertEqual((got["action"], got["url"]), ("mark_paid", "https://kovai-mart.myshopify.com/admin/orders/555"))
		self.assertTrue(got["note"])

	def test_unpaid_draft_offers_collect_payment_on_the_draft(self):
		self.use(config.OAUTH_MODE)
		got = self.action(payment_status="Unpaid", external_order_id=None, external_draft_id="777")
		self.assertEqual((got["action"], got["url"]), ("collect", "https://kovai-mart.myshopify.com/admin/draft_orders/777"))

	def test_nothing_when_not_needed_or_not_allowed(self):
		self.use(config.OAUTH_MODE)
		cases = {
			"already paid in Shopify": {"store_marked_paid_on": "2026-11-17 10:00:00"},
			"not sent to Shopify": {"external_order_id": None},
			"draft order": {"docstatus": 0},
			"partly paid": {"payment_status": "Partially Paid"},
			"rep": {"manager": False},
			"retailer login": {"retailer": True},
		}
		for label, values in cases.items():
			with self.subTest(label):
				self.assertIsNone(self.action(**values))

	def test_direct_installs_get_no_button(self):
		self.use(config.CLIENT_CREDENTIALS_MODE)
		self.assertIsNone(self.action())
