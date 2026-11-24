# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from field_sales.utils.store import is_multi_store


class PlatformProduct(Document):
	def autoname(self):
		"""
		On the shared site the name carries the store (``Shopify-ACME-4711``): two stores may hold
		the same platform variant id (WooCommerce / Odoo ids are per shop; one shop connected as
		two Stores), and one name for both would make the second store's sync fail on insert.
		Single-store sites keep the doctype's ``{platform}-{external_variant_id}``. ``store`` is
		set by ``stamp_store`` (``before_insert``, which runs before naming). Code finds products
		by (store, platform, external_variant_id), never by building the name.
		"""
		store = self.get("store")
		if is_multi_store() and store:
			self.name = f"{self.platform}-{store}-{self.external_variant_id}"

	def on_trash(self):
		# Product Stock links to the product, which would otherwise block the delete.
		for name in frappe.get_all("Product Stock", filters={"product": self.name}, pluck="name"):
			frappe.delete_doc("Product Stock", name, ignore_permissions=True, force=True)
