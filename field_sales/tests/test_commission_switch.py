# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.commission import commission_enabled
from field_sales.commission.api import (
	assign_commission_plan,
	create_adjustment,
	generate_statements,
	save_commission_plan,
)
from field_sales.commission.engine import enqueue_create_entries, reconcile_order
from field_sales.commission.statements import generate_monthly_statements
from field_sales.frontend_api import get_session


class TestCommissionSwitch(IntegrationTestCase):
	"""Settings → Commission: a company that pays no commission."""

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def turn_off(self):
		frappe.db.set_single_value("Store Connection", "enable_commission", 0)

	def test_on_when_never_saved(self):
		frappe.db.delete("Singles", {"doctype": "Store Connection", "field": "enable_commission"})
		self.assertTrue(commission_enabled())

	def test_on_and_off(self):
		frappe.db.set_single_value("Store Connection", "enable_commission", 1)
		self.assertTrue(commission_enabled())
		self.turn_off()
		self.assertFalse(commission_enabled())

	def test_off_creates_no_entries(self):
		self.turn_off()
		with patch("field_sales.commission.engine.frappe.enqueue") as enqueue:
			enqueue_create_entries("FO-TEST-1")
		enqueue.assert_not_called()
		with patch("field_sales.commission.engine.get_connector") as connector:
			result = reconcile_order("123456")
		self.assertEqual(result, {"note": "Ignored: commission is turned off"})
		connector.assert_not_called()

	def test_off_makes_no_monthly_statements(self):
		self.turn_off()
		with patch("field_sales.commission.statements.build_statement") as build:
			generate_monthly_statements()
		build.assert_not_called()

	def test_off_refuses_new_commission_work(self):
		self.turn_off()
		calls = {
			"save plan": lambda: save_commission_plan({"plan_name": "X", "rule_type": "Flat %"}),
			"assign plan": lambda: assign_commission_plan("rep@example.com", None, 0),
			"adjustment": lambda: create_adjustment("rep@example.com", 10, "test"),
			"statements": lambda: generate_statements("2026-09"),
		}
		for label, call in calls.items():
			with self.subTest(label):
				self.assertRaisesRegex(frappe.ValidationError, "Commission is turned off", call)

	def test_session_tells_the_app(self):
		self.assertTrue(get_session()["commission_enabled"])
		self.turn_off()
		self.assertFalse(get_session()["commission_enabled"])
