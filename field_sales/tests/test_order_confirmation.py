# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import socket
import time
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from field_sales.sync import confirmation
from field_sales.sync.confirmation import resend_order_confirmation, send_order_confirmation

SHOP = "test-shop.myshopify.com"
PRINT_FORMAT = "Field Sales Invoice"


class TestOrderConfirmation(IntegrationTestCase):
	def setUp(self):
		# set_single_value does not validate; everything rolls back after each test.
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": SHOP,
				"send_order_confirmation": 1,
				"company_name": "Test Traders",
				"company_address": "1 Mill Road\nCoimbatore",
				"company_state": "Tamil Nadu",
				"company_gstin": "33AAAAA0000A1Z5",
			},
		)
		self.make_outgoing_account()
		self.users = 0
		self.rep = self.make_rep()
		self.retailer = self.make_retailer()
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Confirmation product",
				"external_product_id": "9100",
				"external_variant_id": "91",
				"status": "Active",
				"price": 100,
				"hsn_code": "1006",
			}
		).insert(ignore_permissions=True)
		self.order = self.make_order()

		# Rendering a PDF needs wkhtmltopdf and the web server (it fetches the print CSS); the
		# send tests only need a PDF-shaped attachment, so the renderer is stubbed for them.
		self.pdf = patch("frappe.utils.pdf.get_pdf", return_value=b"%PDF-1.4 test invoice")
		self.pdf.start()
		self.addCleanup(self.pdf.stop)
		# No Razorpay calls here; the link itself is tested in test_razorpay_links.
		self.pay_link = patch.object(confirmation, "link_for_confirmation", return_value=None)
		self.pay_link.start()
		self.addCleanup(self.pay_link.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		# IntegrationTestCase only rolls back once per class: undo this test's rows here.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- builders ----------------------------------------------------------

	def make_outgoing_account(self):
		"""sendmail needs a default outgoing account to queue anything; nothing is delivered in tests."""
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": "Confirmation Test Outgoing",
				"email_id": "confirm.outgoing@example.com",
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"smtp_server": "smtp.example.com",
				"smtp_port": 587,
				"use_tls": 1,
				"password": "unused",
			}
		).insert(ignore_permissions=True)

	def make_rep(self, reports_to=None, role="Field Sales Rep"):
		self.users += 1
		email = f"confirm.rep{self.users}@example.com"
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": f"ConfirmRep{self.users}", "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles(role)
		return frappe.get_doc(
			{"doctype": "Sales Rep", "user": email, "reports_to": reports_to}
		).insert(ignore_permissions=True)

	def make_retailer(self, email="buyer@example.com", state="Tamil Nadu"):
		return frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Confirmation Stores",
				"phone": "9876543210",
				"address_line1": "5 Market Street",
				"city": "Salem",
				"state": state,
				"gstin": "33BBBBB1111B1Z5",
				"email": email,
			}
		).insert(ignore_permissions=True)

	def make_order(self, retailer=None):
		"""A submitted Field Order the store has confirmed (the push is not run)."""
		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": (retailer or self.retailer).name,
				"sales_rep": self.rep.name,
				"order_date": nowdate(),
				"discount_percent": 10,
				"items": [{"product": self.product.name, "qty": 2, "rate": 100}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Field Order",
			doc.name,
			{"docstatus": 1, "external_order_id": "5001", "store_order_name": "#1006", "platform": "Shopify"},
		)
		return frappe.get_doc("Field Order", doc.name)

	def values(self, *fields):
		return frappe.db.get_value("Field Order", self.order.name, list(fields), as_dict=True)

	def queued(self):
		return frappe.get_all(
			"Email Queue",
			filters={"reference_doctype": "Field Order", "reference_name": self.order.name},
			fields=["name", "message", "status"],
			order_by="creation",
		)

	# -- sending -----------------------------------------------------------

	def test_sends_once_with_invoice_pdf(self):
		send_order_confirmation(self.order.name)

		queued = self.queued()
		self.assertEqual(len(queued), 1)
		values = self.values("invoice_no", "invoice_date", "confirmation_sent_on", "confirmation_status")
		self.assertTrue(values.invoice_no.startswith("INV-"))
		self.assertEqual(str(values.invoice_date), nowdate())
		self.assertIsNotNone(values.confirmation_sent_on)
		self.assertEqual(values.confirmation_status, "Sent to buyer@example.com")

		recipients = frappe.get_all("Email Queue Recipient", filters={"parent": queued[0].name}, pluck="recipient")
		self.assertEqual(recipients, ["buyer@example.com"])
		message = queued[0].message
		self.assertIn(f"{values.invoice_no}.pdf", message)
		self.assertIn("Order confirmed: #1006", message)

	def test_store_checkout_link_is_the_pay_now_button(self):
		# Razorpay gave no link (as on App Store installs): the store's checkout pays.
		url = "https://test-shop.myshopify.com/checkouts/c/abc123"
		frappe.db.set_value("Field Order", self.order.name, "store_checkout_url", url)
		send_order_confirmation(self.order.name)

		message = self.queued()[0].message
		self.assertIn(url, message)
		self.assertIn("Confirmation Stores", message)

	def test_payment_link_is_in_the_email(self):
		link = {"url": "https://rzp.io/i/plink_test", "id": "plink_test", "expires_on": None}
		with patch.object(confirmation, "link_for_confirmation", return_value=link):
			send_order_confirmation(self.order.name)
		message = self.queued()[0].message
		self.assertIn("https://rzp.io/i/plink_test", message)
		self.assertIn("Pay Now", message)

	def test_amounts_use_the_site_currency(self):
		with patch.object(confirmation, "site_currency", return_value="USD"):
			send_order_confirmation(self.order.name)
		self.assertIn("$", self.queued()[0].message)

	def test_status_says_when_the_payment_link_failed(self):
		def refuse(doc):
			doc.flags.payment_link_error = "amount exceeds maximum amount allowed."

		with patch.object(confirmation, "link_for_confirmation", side_effect=refuse):
			status = send_order_confirmation(self.order.name)
		self.assertEqual(status, "Sent to buyer@example.com without payment link")
		self.assertNotIn("Pay Now", self.queued()[0].message)

	def test_no_payment_block_without_a_link(self):
		send_order_confirmation(self.order.name)
		self.assertNotIn("Pay Now", self.queued()[0].message)

	def test_second_call_does_not_send_again(self):
		send_order_confirmation(self.order.name)
		send_order_confirmation(self.order.name)

		self.assertEqual(len(self.queued()), 1)

	def test_resend_sends_again_with_the_same_invoice_no(self):
		send_order_confirmation(self.order.name)
		first = self.values("invoice_no", "invoice_date")

		send_order_confirmation(self.order.name, resend=True)

		self.assertEqual(len(self.queued()), 2)
		self.assertEqual(self.values("invoice_no", "invoice_date"), first)

	def test_skipped_without_customer_email(self):
		frappe.db.set_value("Retailer", self.retailer.name, "email", None)

		send_order_confirmation(self.order.name)

		self.assertEqual(self.queued(), [])
		values = self.values("confirmation_status", "confirmation_sent_on")
		self.assertEqual(values.confirmation_status, "Skipped: customer has no email")
		self.assertIsNone(values.confirmation_sent_on)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Field Order", "reference_name": self.order.name, "comment_type": "Comment"},
			)
		)

	def test_skipped_when_turned_off(self):
		frappe.db.set_single_value("Store Connection", "send_order_confirmation", 0)

		send_order_confirmation(self.order.name)

		self.assertEqual(self.queued(), [])
		values = self.values("confirmation_status", "invoice_no")
		self.assertIn("turned off", values.confirmation_status)
		self.assertFalse(values.invoice_no)

	def test_error_is_recorded_without_raising(self):
		with patch("field_sales.sync.confirmation.frappe.sendmail", side_effect=Exception("mail is down")):
			send_order_confirmation(self.order.name)

		values = self.values("confirmation_status", "confirmation_sent_on", "invoice_no")
		self.assertEqual(values.confirmation_status, "Error: mail is down")
		self.assertIsNone(values.confirmation_sent_on)
		# The failed attempt did not keep an invoice number.
		self.assertFalse(values.invoice_no)

	def test_resend_is_queued_not_sent_inline(self):
		send_order_confirmation(self.order.name)

		with patch("field_sales.sync.confirmation.frappe.enqueue") as enqueue, patch.object(frappe.db, "commit"):
			result = resend_order_confirmation(self.order.name)

		self.assertEqual(result, {"status": "Sending…", "queued": True})
		self.assertEqual(self.values("confirmation_status").confirmation_status, "Sending…")
		self.assertEqual(len(self.queued()), 1)
		args, kwargs = enqueue.call_args
		self.assertEqual(args, ("field_sales.sync.confirmation.send_order_confirmation",))
		self.assertEqual(kwargs["field_order"], self.order.name)
		self.assertTrue(kwargs["resend"])
		self.assertTrue(kwargs["deduplicate"])
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertEqual(kwargs["timeout"], confirmation.JOB_TIMEOUT)
		# Not the automatic job's id, or deduplicate could drop the resend.
		with patch("field_sales.sync.confirmation.frappe.enqueue") as auto:
			confirmation.enqueue_order_confirmation(self.order.name)
		self.assertNotEqual(auto.call_args.kwargs["job_id"], kwargs["job_id"])

	def test_resend_job_sends_and_replaces_sending_status(self):
		send_order_confirmation(self.order.name)
		self.order.db_set("confirmation_status", "Sending…")

		send_order_confirmation(self.order.name, resend=True)

		self.assertEqual(self.values("confirmation_status").confirmation_status, "Sent to buyer@example.com")
		self.assertEqual(len(self.queued()), 2)

	def test_pdf_timeout_is_recorded_and_keeps_no_invoice_no(self):
		def slow(html, *args, **kwargs):
			time.sleep(4)
			return b"%PDF"

		with patch("frappe.utils.pdf.get_pdf", side_effect=slow), patch.object(confirmation, "PDF_TIMEOUT", 1):
			start = time.monotonic()
			send_order_confirmation(self.order.name)
			elapsed = time.monotonic() - start

		self.assertLess(elapsed, 3.5)
		values = self.values("confirmation_status", "confirmation_sent_on", "invoice_no")
		self.assertEqual(values.confirmation_status, confirmation.PDF_TIMEOUT_STATUS)
		self.assertIsNone(values.confirmation_sent_on)
		self.assertFalse(values.invoice_no)
		self.assertEqual(self.queued(), [])

	# -- resend permission -------------------------------------------------

	def test_rep_and_their_manager_can_resend(self):
		manager = self.make_rep(role="Sales Manager")
		self.rep.db_set("reports_to", manager.name)
		frappe.clear_cache()
		for user in (self.rep.user, manager.user):
			frappe.set_user(user)
			with patch("field_sales.sync.confirmation.frappe.enqueue") as enqueue, patch.object(
				frappe.db, "commit"
			):
				result = resend_order_confirmation(self.order.name)
			self.assertEqual(result, {"status": "Sending…", "queued": True})
			enqueue.assert_called_once()
		frappe.set_user("Administrator")
		self.assertEqual(self.queued(), [])  # nothing is sent in the request itself

	def test_user_without_access_cannot_resend(self):
		other = self.make_rep()
		frappe.set_user(other.user)

		with self.assertRaises(frappe.PermissionError):
			resend_order_confirmation(self.order.name)

		frappe.set_user("Administrator")
		self.assertEqual(self.queued(), [])

	# -- invoice print -----------------------------------------------------

	def html(self, order=None):
		return frappe.get_print("Field Order", (order or self.order).name, print_format=PRINT_FORMAT)

	def test_invoice_splits_into_cgst_and_sgst_within_the_state(self):
		html = self.html()

		self.assertIn("TAX INVOICE", html)
		self.assertIn("CGST (2.5%)", html)
		self.assertIn("SGST (2.5%)", html)
		self.assertNotIn("IGST", html)
		# 200 net, 20 discount, 10 tax (5 each); HSN, seller and buyer details, amount in words.
		self.assertIn("1006", html)
		self.assertIn("Test Traders", html)
		self.assertIn("33AAAAA0000A1Z5", html)
		self.assertIn("33BBBBB1111B1Z5", html)
		self.assertIn("INR One Hundred And Ninety only.", html)  # grand total 190 in words

	def test_state_match_ignores_case_and_spaces(self):
		frappe.db.set_single_value("Store Connection", "company_state", "  tamil nadu ")

		self.assertIn("CGST", self.html())

	def test_invoice_uses_igst_for_another_state(self):
		frappe.db.set_value("Retailer", self.retailer.name, "state", "Kerala")

		html = self.html()

		self.assertIn("IGST (5.0%)", html)
		self.assertNotIn("CGST", html)

	def test_invoice_without_company_details_still_renders(self):
		frappe.db.set_single_value(
			"Store Connection",
			{"company_name": "", "company_address": "", "company_state": "", "company_gstin": ""},
		)

		html = self.html()

		self.assertIn("TAX INVOICE", html)
		self.assertIn("IGST", html)  # no seller state: cannot call it an in-state sale

	def test_invoice_renders_as_pdf(self):
		self.pdf.stop()
		self.addCleanup(self.pdf.start)  # the cleanup above stops it once more
		port = frappe.conf.webserver_port
		with socket.socket() as probe:
			probe.settimeout(1)
			if probe.connect_ex(("127.0.0.1", port)):
				self.skipTest(f"wkhtmltopdf fetches the print CSS from the web server (port {port}), which is not running")
		# The test site has no host_name, so the stylesheet links would point at port 80.
		frappe.local.conf.host_name = f"http://{frappe.local.site}:{port}"

		pdf = frappe.get_print("Field Order", self.order.name, print_format=PRINT_FORMAT, as_pdf=True)

		self.assertTrue(pdf.startswith(b"%PDF"))
