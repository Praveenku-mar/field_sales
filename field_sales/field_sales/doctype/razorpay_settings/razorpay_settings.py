# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from field_sales.payments.razorpay import WEBHOOK_PATH


class RazorpaySettings(Document):
	def onload(self):
		# Shown so it can be pasted into the Razorpay Dashboard; always this site's URL.
		self.webhook_url = frappe.utils.get_url(WEBHOOK_PATH)

	def validate(self):
		self.key_id = (self.key_id or "").strip()
		self.callback_url = (self.callback_url or "").strip()
		self.webhook_url = frappe.utils.get_url(WEBHOOK_PATH)
