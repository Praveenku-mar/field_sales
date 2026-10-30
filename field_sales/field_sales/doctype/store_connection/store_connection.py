# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import remove_encrypted_password

from field_sales.connectors import CONNECTORS

# Set by the Shopify install flow: store_url is then filled by it, not typed by the user.
OAUTH_MODE = "OAuth"


class StoreConnection(Document):
	def validate(self):
		self._normalize_store_url()
		self._clear_token_on_credential_change()
		self._validate_locations()
		# Read here: after the save the field only holds the masked value.
		self.flags.secret_entered = any(
			bool(self.get(f)) and not self.is_dummy_password(self.get(f))
			for f in ("shopify_client_secret", "woo_consumer_secret", "odoo_api_key")
		)

	def on_update(self):
		if self.flags.clear_stored_tokens:
			for field in ("access_token", "refresh_token"):
				remove_encrypted_password(self.doctype, self.name, field)
		self._queue_shop_settings_sync()

	def _queue_shop_settings_sync(self):
		"""
		Fetch the store's currency and time zone in the background after the connection changes.

		Never runs inline, so wrong credentials can't block or fail the save.
		"""
		if not (self.enabled and self.platform in CONNECTORS and self.match_shop_settings):
			return
		changed = (
			any(
				self.has_value_changed(f)
				for f in (
					"enabled",
					"platform",
					"store_url",
					"shopify_client_id",
					"woo_consumer_key",
					"odoo_database",
					"odoo_username",
					"match_shop_settings",
				)
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

		fields = ["platform", "store_url", "shopify_auth_mode"]
		secret_changed = False
		if self.shopify_auth_mode != OAUTH_MODE:
			# The app credentials on this form only matter for the client credentials grant.
			fields.append("shopify_client_id")
			secret = self.shopify_client_secret or ""
			secret_changed = (bool(secret) and not self.is_dummy_password(secret)) or (
				not secret and bool(before.get("shopify_client_secret"))
			)
		changed = secret_changed or any(self.get(f) != before.get(f) for f in fields)
		if changed and (before.get("access_token") or before.get("token_expiry") or before.get("refresh_token")):
			self.access_token = None
			self.token_expiry = None
			self.refresh_token = None
			self.refresh_token_expiry = None
			self.flags.clear_stored_tokens = True

	def _normalize_store_url(self):
		"""
		Let the platform's connector clean up and check Store URL (``normalize_store_url``).

		In OAuth mode the install flow sets Store URL; a typed change is undone.
		"""
		if self.shopify_auth_mode == OAUTH_MODE and not self.flags.from_store_install and not self.is_new():
			before = self.get_doc_before_save()
			if before and before.store_url:
				self.store_url = before.store_url

		connector_cls = CONNECTORS.get(self.platform)
		if connector_cls and self.store_url:
			self.store_url = connector_cls.normalize_store_url(self.store_url)
