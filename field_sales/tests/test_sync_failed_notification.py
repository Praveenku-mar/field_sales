# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.email.doctype.notification.notification import get_context
from frappe.tests import IntegrationTestCase


class TestSyncFailedNotification(IntegrationTestCase):
	"""The "Field Order Sync Failed" email must show rep- and store-supplied text as text."""

	def render(self, **values):
		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"name": "FO-TEST-00001",
				"retailer": "RET-TEST",
				"retailer_name": "Shop",
				"sales_rep": "rep@example.com",
				"sync_attempts": 5,
				"sync_error": "Store refused the order",
				**values,
			}
		)
		notification = frappe.get_doc("Notification", "Field Order Sync Failed")
		# The same call Notification.send_an_email makes for the body.
		return frappe.render_template(notification.message, get_context(doc), restrict_globals=True)

	def test_retailer_name_html_is_escaped(self):
		html = self.render(retailer_name='<b>Bold</b> <a href="https://evil.example">Pay here</a>')
		self.assertIn("&lt;b&gt;Bold&lt;/b&gt;", html)
		self.assertNotIn('<a href="https://evil.example">', html)

	def test_sync_error_and_rep_are_escaped(self):
		html = self.render(sync_error="<img src=x onerror=alert(1)>", sales_rep="<i>rep</i>")
		self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
		self.assertIn("&lt;i&gt;rep&lt;/i&gt;", html)
		self.assertNotIn("<img", html)

	def test_retailer_id_is_used_when_name_is_empty(self):
		html = self.render(retailer_name=None, retailer="RET-<b>9</b>")
		self.assertIn("RET-&lt;b&gt;9&lt;/b&gt;", html)

	def test_order_link_still_works(self):
		html = self.render()
		self.assertIn(f'href="{frappe.utils.get_url_to_form("Field Order", "FO-TEST-00001")}"', html)
		self.assertIn(">FO-TEST-00001</a>", html)
