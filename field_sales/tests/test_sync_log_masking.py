# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.field_sales.doctype.sync_log.sync_log import _MAX_PAYLOAD_CHARS, _mask_payload

TOKEN = "shpat_SECRETTOKEN0123456789abcdef"


class TestSyncLogMasking(IntegrationTestCase):
	"""Secrets never reach a Sync Log (Sales Managers can read them)."""

	def tearDown(self):
		frappe.db.rollback()

	def insert(self, payload: str):
		doc = frappe.get_doc(
			{
				"doctype": "Sync Log",
				"platform": "Shopify",
				"operation": "Other",
				"status": "Success",
				"request_payload": payload,
			}
		).insert(ignore_permissions=True)
		return frappe.db.get_value("Sync Log", doc.name, "request_payload")

	def test_access_and_refresh_tokens_are_masked(self):
		masked = _mask_payload('{"access_token": "abc", "refresh_token": "def"}')
		self.assertEqual(masked, '{"access_token": "****", "refresh_token": "****"}')

	def test_token_at_the_truncation_point_is_masked(self):
		# The token's value starts just before the cut, so truncating first would
		# drop its closing quote and leave the start of the token in the log.
		key = '"access_token": "'
		filler = "x" * (_MAX_PAYLOAD_CHARS - 10 - len(key) - len('{"pad": "",'))
		payload = '{"pad": "' + filler + '", ' + key + TOKEN + '", "tail": "' + "y" * 10_000 + '"}'
		self.assertGreater(len(payload), 60_000)
		self.assertLess(payload.index(TOKEN), _MAX_PAYLOAD_CHARS)
		self.assertGreater(payload.index(TOKEN) + len(TOKEN), _MAX_PAYLOAD_CHARS)

		stored = self.insert(payload)
		self.assertNotIn("shpat_", stored)
		self.assertNotIn("SECRET", stored)
		self.assertIn('"access_token": "****"', stored)
		self.assertTrue(stored.endswith("[truncated]"))
		self.assertLessEqual(len(stored), _MAX_PAYLOAD_CHARS + len("\n… [truncated]"))

	def test_short_payload_is_masked_and_kept_whole(self):
		stored = self.insert(json.dumps({"refresh_token": TOKEN, "amount": 500}))
		self.assertEqual(json.loads(stored), {"refresh_token": "****", "amount": 500})

	# -- personal data (Level 2) ------------------------------------------------

	def test_personal_fields_are_masked_ids_and_errors_kept(self):
		payload = json.dumps(
			{
				"input": {
					"id": "gid://shopify/Customer/42",
					"firstName": "Asha",
					"lastName": "Kumar",
					"email": "asha@example.com",
					"phone": "+919876543210",
					"addresses": [
						{
							"address1": "1 Market St",
							"address2": 'Near "Big" Temple',
							"zip": "636502",
							"city": "Salem",
						}
					],
				},
				"customer": {"contact": "+919876543210", "name": "Asha Stores"},
				"userErrors": [{"field": ["email"], "message": "Email has already been taken"}],
				"amount": 500,
			}
		)
		stored = json.loads(self.insert(payload))
		customer = stored["input"]
		for key in ("firstName", "lastName", "email", "phone"):
			self.assertEqual(customer[key], "[redacted]", key)
		address = customer["addresses"][0]
		self.assertEqual((address["address1"], address["address2"], address["zip"]), ("[redacted]",) * 3)
		self.assertEqual(stored["customer"]["contact"], "[redacted]")
		# Ids, amounts, the error text and non-personal fields stay.
		self.assertEqual(customer["id"], "gid://shopify/Customer/42")
		self.assertEqual(address["city"], "Salem")
		self.assertEqual(stored["userErrors"][0]["message"], "Email has already been taken")
		self.assertEqual(stored["amount"], 500)

	def test_snake_case_names_are_masked(self):
		masked = _mask_payload('{"first_name": "A", "last_name": "B", "zip": null, "email": true}')
		self.assertEqual(
			json.loads(masked),
			{"first_name": "[redacted]", "last_name": "[redacted]", "zip": None, "email": True},
		)


class TestSyncLogRetention(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def log(self, status, days_old):
		from frappe.utils import add_days, now_datetime

		doc = frappe.get_doc(
			{"doctype": "Sync Log", "platform": "Shopify", "operation": "Other", "status": status}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Sync Log", doc.name, "creation", add_days(now_datetime(), -days_old), update_modified=False
		)
		return doc.name

	def test_failed_logs_follow_the_retention_setting(self):
		from field_sales.field_sales.doctype.sync_log.sync_log import clear_old_logs

		old_failed, new_failed = self.log("Failed", 200), self.log("Failed", 10)
		old_retrying, old_success = self.log("Retrying", 200), self.log("Success", 40)
		frappe.db.set_single_value("Store Connection", "failed_log_retention_days", 180)
		with patch.object(frappe.db, "commit"):
			clear_old_logs()
		self.assertFalse(frappe.db.exists("Sync Log", old_failed))
		self.assertFalse(frappe.db.exists("Sync Log", old_retrying))
		self.assertFalse(frappe.db.exists("Sync Log", old_success))
		self.assertTrue(frappe.db.exists("Sync Log", new_failed))

		kept = self.log("Failed", 400)
		frappe.db.set_single_value("Store Connection", "failed_log_retention_days", 0)  # keep forever
		with patch.object(frappe.db, "commit"):
			clear_old_logs()
		self.assertTrue(frappe.db.exists("Sync Log", kept))
