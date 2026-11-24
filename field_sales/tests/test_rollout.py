# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Phase 9 rollout: product syncs staggered per scheduler tick, and the shared-site readiness checks."""

from unittest.mock import patch

import frappe
from frappe.utils import add_days, now_datetime

from field_sales.tests.test_store_isolation import MULTI, StoreIsolationCase


class TestProductSyncStagger(StoreIsolationCase):
	def setUp(self):
		super().setUp()
		frappe.db.savepoint("product_stagger")
		for store in ("ISA", "ISB"):
			frappe.db.set_value(
				"Store",
				store,
				{"enabled": 1, "default_location_id": f"L-{store}", "product_sync_frequency": "Hourly"},
			)
		# Store A last synced two days ago; store B never.
		frappe.db.set_value("Sync Log", self.a.sync_log, "creation", add_days(now_datetime(), -2))
		frappe.db.delete("Sync Log", {"store": "ISB", "operation": "Product Sync"})

	def tearDown(self):
		frappe.db.rollback(save_point="product_stagger")
		super().tearDown()

	def run_tick(self, queued_result=True):
		from field_sales.sync import products

		called = []

		def enqueue(store, **kwargs):
			called.append(store)
			return queued_result(store) if callable(queued_result) else queued_result

		with patch.object(products, "enqueue_sync", side_effect=enqueue):
			products.scheduled_sync()
		return [s for s in called if s in ("ISA", "ISB")]

	def test_a_tick_queues_the_most_overdue_stores_up_to_its_limit(self):
		with patch.dict(frappe.conf, {"field_sales_product_syncs_per_tick": 1}):
			self.assertEqual(self.run_tick(), ["ISB"])  # never synced comes first
		with patch.dict(frappe.conf, {"field_sales_product_syncs_per_tick": 5}):
			self.assertEqual(self.run_tick(), ["ISB", "ISA"])

	def test_a_sync_already_queued_does_not_use_up_the_limit(self):
		with patch.dict(frappe.conf, {"field_sales_product_syncs_per_tick": 1}):
			called = self.run_tick(lambda store: None if store == "ISB" else object())
		self.assertEqual(called, ["ISB", "ISA"])


class TestSharedSiteChecks(StoreIsolationCase):
	def checks(self, conf) -> dict:
		from field_sales.utils.rollout import shared_site_checks

		with patch.dict(frappe.conf, conf):
			return {label: (level, detail) for level, label, detail in shared_site_checks()}

	def test_a_configured_shared_site_passes_the_app_checks(self):
		checks = self.checks(
			{
				**MULTI,
				"shopify_app_client_id": "id",
				"shopify_app_client_secret": "secret",
				"shopify_app_url": "https://fieldsales.example.org",
			}
		)
		for label in ("Shared site flag", "App client id", "App client secret", "Public address"):
			self.assertEqual(checks[label][0], "ok", (label, checks[label]))

	def test_a_site_without_the_flag_or_keys_is_not_ready(self):
		from field_sales.utils.rollout import has_failures, shared_site_checks

		with patch.dict(
			frappe.conf,
			{"field_sales_multi_store": 0, "shopify_app_client_id": "", "shopify_app_url": "http://plain.example.org"},
		):
			checks = shared_site_checks()
		self.assertTrue(has_failures(checks))
		failed = {label for level, label, _detail in checks if level == "fail"}
		self.assertTrue({"Shared site flag", "App client id", "Public address"} <= failed, failed)

	def test_system_managers_without_the_vendor_role_are_listed(self):
		from field_sales.tests.store_fixtures import make_user
		from field_sales.utils.store import assign_user_to_store

		roleless = make_user("rollout.sysman@example.com", ["System Manager"])
		in_store = make_user("rollout.store-sysman@example.com", ["System Manager"])
		assign_user_to_store(in_store, "ISA")

		checks = self.checks(MULTI)
		self.assertEqual(checks["Vendor staff"][0], "ok")
		self.assertIn(self.vendor, checks["Vendor staff"][1])
		self.assertEqual(checks["System Managers in no store"][0], "warn")
		self.assertIn(roleless, checks["System Managers in no store"][1])
		self.assertEqual(checks["Store users with site-wide roles"][0], "fail")
		self.assertIn(in_store, checks["Store users with site-wide roles"][1])
