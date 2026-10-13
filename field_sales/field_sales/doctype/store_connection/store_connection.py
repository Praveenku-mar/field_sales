# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from field_sales.connectors.shopify import is_myshopify_domain, normalize_shop_domain


class StoreConnection(Document):
	def validate(self):
		self._normalize_shopify_store_url()
		self._clear_token_on_credential_change()

	def _clear_token_on_credential_change(self):
		"""Drop the cached access token when the store or app credentials change."""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return

		changed = any(
			self.get(f) != before.get(f) for f in ("platform", "store_url", "shopify_client_id")
		) or not self.is_dummy_password(self.shopify_client_secret or "")
		if changed and (before.get("access_token") or before.get("token_expiry")):
			self.access_token = None
			self.token_expiry = None

	def _normalize_shopify_store_url(self):
		"""Store the bare ``*.myshopify.com`` domain, whatever form of URL was pasted."""
		if self.platform != "Shopify" or not self.store_url:
			return

		domain = normalize_shop_domain(self.store_url)
		if not is_myshopify_domain(domain):
			frappe.throw(
				_(
					"Store URL must be the shop's myshopify.com domain (e.g. your-store.myshopify.com). "
					"You can find it in Shopify admin under Settings > Domains."
				),
				title=_("Invalid Store URL"),
			)
		self.store_url = domain
