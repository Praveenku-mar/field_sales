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
		self._validate_locations()
		# Read here: after the save the field only holds the masked value.
		self.flags.secret_entered = bool(self.shopify_client_secret) and not self.is_dummy_password(
			self.shopify_client_secret
		)

	def on_update(self):
		self._queue_shop_settings_sync()

	def _queue_shop_settings_sync(self):
		"""
		Fetch the store's currency and time zone in the background after the connection changes.

		Never runs inline, so wrong credentials can't block or fail the save.
		"""
		if not (self.enabled and self.platform == "Shopify" and self.match_shop_settings):
			return
		changed = (
			any(
				self.has_value_changed(f)
				for f in ("enabled", "store_url", "shopify_client_id", "match_shop_settings")
			)
			or self.flags.secret_entered
			or not self.shop_currency
		)
		if not changed:
			return
		frappe.enqueue(
			"field_sales.sync.shop_settings.scheduled_sync",
			queue="short",
			enqueue_after_commit=True,
			job_id="field_sales_shop_settings_sync",
			deduplicate=True,
			notify_user=frappe.session.user,
		)

	def _validate_locations(self):
		"""
		Location ids are unique, and the default location is always a ``Sell from here`` row
		(added or ticked for you, rather than refusing the save).
		"""
		seen = set()
		for row in self.locations:
			row.location_id = (row.location_id or "").strip()
			if row.location_id in seen:
				frappe.throw(_("Row {0}: Location {1} is listed twice").format(row.idx, row.location_id))
			seen.add(row.location_id)

		default_id = (self.default_location_id or "").strip()
		self.default_location_id = default_id
		if not default_id:
			return

		default_row = next((r for r in self.locations if r.location_id == default_id), None)
		if not default_row:
			default_row = self.append(
				"locations", {"location_id": default_id, "location_name": self.default_location_name}
			)
		default_row.sell_from = 1
		if default_row.location_name and not self.default_location_name:
			self.default_location_name = default_row.location_name

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
