# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.permissions import field_order_query, visit_query
from field_sales.tests import integration_fixtures

PASSWORD = "it-fixture-password-123"


class TestIntegrationFixtures(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Store Connection", {"enabled": 0, "platform": "Shopify"})

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def seed(self):
		with patch.dict(frappe.conf, {"field_sales_integration_site": 1}):
			return integration_fixtures.seed(PASSWORD, commit=False)

	def test_refuses_sites_that_did_not_opt_in(self):
		with patch.dict(frappe.conf, {"field_sales_integration_site": 0}):
			with self.assertRaises(frappe.ValidationError):
				integration_fixtures.seed(PASSWORD, commit=False)
		self.assertFalse(frappe.db.exists("User", "it.rep.a@fieldsales.test"))

	def test_rejects_weak_password(self):
		with patch.dict(frappe.conf, {"field_sales_integration_site": 1}):
			with self.assertRaises(frappe.ValidationError):
				integration_fixtures.seed("short", commit=False)

	def test_seed_is_idempotent_and_builds_the_permission_matrix(self):
		first = self.seed()
		second = self.seed()
		self.assertEqual(first["reps"], second["reps"])
		self.assertEqual(first["orders"], second["orders"])
		self.assertEqual(frappe.db.count("Visit", {"notes": integration_fixtures.MARKER}), 2)

		def visible(query_fn, doctype, user):
			cond = query_fn(user)
			where = f"where {cond}" if cond else ""
			return set(frappe.db.sql_list(f"select name from `tab{doctype}` {where}"))

		orders = first["orders"]
		rep_a_orders = visible(field_order_query, "Field Order", first["users"]["rep_a"])
		self.assertIn(orders["rep_a_draft"], rep_a_orders)
		self.assertNotIn(orders["rep_b_draft"], rep_a_orders)

		manager_visits = visible(visit_query, "Visit", first["users"]["manager"])
		self.assertIn(second["visits"]["rep_a"], manager_visits)
		self.assertNotIn(second["visits"]["rep_b"], manager_visits)

		retailer_orders = visible(field_order_query, "Field Order", first["users"]["retailer"])
		self.assertIn(orders["rep_a_draft"], retailer_orders)
		self.assertNotIn(orders["rep_b_draft"], retailer_orders)
