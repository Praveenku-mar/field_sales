# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import json

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
			{"doctype": "Sync Log", "platform": "Shopify", "operation": "Other", "status": "Success", "request_payload": payload}
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
