# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Production-readiness audit follow-ups: whitelisted methods that write but had no test, the vendor
store picker's search, Privacy Request access, and escaping in the print formats and emails.
"""

import json
from unittest.mock import patch

import frappe

from field_sales.tests.test_store_isolation import MULTI, StoreIsolationCase

XSS = '<img src=x onerror="alert(1)">'


class TestRefreshStock(StoreIsolationCase):
	def test_rep_refreshes_own_draft_with_live_stock(self):
		from field_sales.api import refresh_stock

		def live_stock(products, location_id):
			# Like the real fetch (set_location_stock): the live figure is saved where the order's
			# stored stock is read: the location's Product Stock, or the product at the default one.
			if location_id:
				frappe.db.set_value(
					"Product Stock", {"product": self.a.product, "location_id": location_id}, "available_qty", 7
				)
			else:
				frappe.db.set_value("Platform Product", self.a.product, "available_qty", 7)
			return {self.a.product: 7}

		self.as_user(self.a.rep_user)
		with patch("field_sales.api.fetch_live_stock", side_effect=live_stock):
			rows = refresh_stock(self.a.order)
		self.assertEqual(rows[0]["available_qty"], 7)
		self.assertEqual(
			frappe.db.get_value("Field Order Item", {"parent": self.a.order}, "available_qty"), 7
		)

	def test_other_stores_order_is_refused(self):
		from field_sales.api import refresh_stock

		self.as_user(self.a.rep_user)
		with patch("field_sales.api.fetch_live_stock") as fetch, self.assertRaises(frappe.PermissionError):
			refresh_stock(self.b.order)
		fetch.assert_not_called()

	def test_submitted_order_is_refused(self):
		from field_sales.api import refresh_stock

		frappe.db.set_value("Field Order", self.a.order, "docstatus", 1)
		try:
			self.as_user(self.a.rep_user)
			with (
				patch("field_sales.api.fetch_live_stock") as fetch,
				self.assertRaises(frappe.ValidationError),
			):
				refresh_stock(self.a.order)
			fetch.assert_not_called()
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Field Order", self.a.order, "docstatus", 0)


class TestRetryAllSyncFailures(StoreIsolationCase):
	def setUp(self):
		super().setUp()
		for team in (self.a, self.b):
			frappe.db.set_value("Field Order", team.order, {"docstatus": 1, "sync_status": "Failed"})

	def tearDown(self):
		frappe.set_user("Administrator")
		for team in (self.a, self.b):
			frappe.db.set_value("Field Order", team.order, {"docstatus": 0, "sync_status": None})
		super().tearDown()

	def test_rep_cannot_retry_all(self):
		from field_sales.api import retry_all_sync_failures

		self.as_user(self.a.rep_user)
		with self.assertRaises(frappe.PermissionError):
			retry_all_sync_failures()

	def test_admin_retries_only_own_stores_failures(self):
		from field_sales.api import retry_all_sync_failures

		self.as_user(self.a.admin)
		with (
			patch("field_sales.api.get_connector"),
			patch("field_sales.api.enqueue_push_order") as push_order,
			patch("field_sales.api.push_retailers"),
		):
			result = retry_all_sync_failures()
		queued = [c.args[0] for c in push_order.call_args_list]
		self.assertIn(self.a.order, queued)
		self.assertNotIn(self.b.order, queued)
		self.assertEqual(result["orders"], len(queued))


class TestResendAdminInvite(StoreIsolationCase):
	def resend(self):
		from field_sales.shopify import store_admin

		with (
			patch.object(store_admin, "store_admin_email", return_value=self.a.admin),
			patch.object(store_admin, "send_admin_invite") as send,
		):
			result = store_admin.resend_admin_invite()
		return result, send

	def test_store_admin_resends_own_stores_invite(self):
		self.as_user(self.a.admin)
		with patch.dict(frappe.conf, MULTI):
			result, send = self.resend()
		send.assert_called_once_with(self.a.admin, "ISA")
		self.assertTrue(result["invited"])

	def test_manager_cannot_resend(self):
		self.as_user(self.a.manager)
		with patch.dict(frappe.conf, MULTI), self.assertRaises(frappe.PermissionError):
			self.resend()

	def test_not_served_on_a_single_store_site(self):
		self.as_user(self.a.admin)
		with self.assertRaises(frappe.PermissionError):
			self.resend()


class TestVendorStoreSearch(StoreIsolationCase):
	def test_search_and_cap(self):
		from field_sales.frontend_api import VENDOR_STORES_LIMIT, get_vendor_stores

		self.as_user(self.vendor)
		with patch.dict(frappe.conf, MULTI):
			self.assertEqual([s.name for s in get_vendor_stores(txt="ISB")], ["ISB"])
			self.assertEqual(len(get_vendor_stores(limit=1)), 1)
			self.assertLessEqual(len(get_vendor_stores(limit=10_000)), VENDOR_STORES_LIMIT)
			# LIKE wildcards in the search are matched literally.
			self.assertEqual(get_vendor_stores(txt="%"), [])


class TestPrivacyRequestAccess(StoreIsolationCase):
	def test_only_store_admin_reads_privacy_requests(self):
		self.assertFalse(frappe.has_permission("Privacy Request", "read", user=self.a.manager))
		self.assertTrue(frappe.has_permission("Privacy Request", "read", user=self.a.admin))


class TestTemplateEscaping(StoreIsolationCase):
	def setUp(self):
		super().setUp()
		# Written without a save, as a sync's direct update would be: Frappe's save-time filter
		# never sees it, so only the template's escaping stands between it and the page.
		self.item = frappe.db.get_value(
			"Field Order Item", {"parent": self.a.order}, ["name", "product_title"], as_dict=True
		)
		self.address = frappe.db.get_value("Retailer", self.a.retailer, "address_line1")
		frappe.db.set_value("Field Order Item", self.item.name, "product_title", XSS)
		frappe.db.set_value("Retailer", self.a.retailer, "address_line1", XSS)

	def tearDown(self):
		# Restored, not rolled back: a rollback would also drop the class's shared records.
		frappe.db.set_value("Field Order Item", self.item.name, "product_title", self.item.product_title)
		frappe.db.set_value("Retailer", self.a.retailer, "address_line1", self.address)
		super().tearDown()

	def test_print_formats_escape_store_and_customer_data(self):
		path = frappe.get_app_path("field_sales", "fixtures", "print_format.json")
		with open(path) as f:
			formats = json.load(f)
		doc = frappe.get_doc("Field Order", self.a.order)
		for print_format in formats:
			html = frappe.render_template(print_format["html"], {"doc": doc})
			self.assertNotIn("<img", html, print_format["name"])
			self.assertIn("&lt;img", html, print_format["name"])

	def test_order_confirmation_email_escapes_item_titles(self):
		html = frappe.get_template("templates/emails/order_confirmation.html").render(
			{
				"_": frappe._,
				"customer": XSS,
				"order_no": "SO-1",
				"invoice_no": "INV-1",
				"items": [{"title": XSS, "variant": XSS, "qty": 1, "amount": "₹ 10"}],
				"company": XSS,
				"sales_rep": XSS,
			}
		)
		self.assertNotIn("<img", html)
		self.assertIn("&lt;img", html)
