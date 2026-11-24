# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import remove_encrypted_password

from field_sales.connectors import CONNECTORS
from field_sales.utils.store import (
	CODE_PATTERN,
	can_be_embedded_user,
	can_change_connection,
	clear_store_cache,
	enqueue_in_store,
	is_multi_store,
)

# Set by the Shopify install flow: store_url is then filled by it, not typed by the user.
OAUTH_MODE = "OAuth"
# Changed only by vendor staff: who embedded requests run as, and the connection credentials.
VENDOR_ONLY_FIELDS = (
	"embedded_user",
	"suspended",
	"platform",
	"shopify_auth_mode",
	"store_url",
	"shopify_client_id",
	"shopify_client_secret",
	"shopify_api_version",
)


class Store(Document):
	def autoname(self):
		self.store_code = (self.store_code or "").strip().upper()
		self.name = self.store_code

	def validate(self):
		self._guard_vendor_fields()
		self._apply_vendor_pause()
		self._validate_embedded_user()
		self._validate_store_code()
		self._validate_order_mode()
		self._remember_tax_mode_choice()
		self._normalize_store_url()
		self._clear_token_on_credential_change()
		self._validate_locations()
		# Read here: after the save the field only holds the masked value.
		self.flags.secret_entered = bool(self.shopify_client_secret) and not self.is_dummy_password(
			self.shopify_client_secret
		)

	def after_insert(self):
		clear_store_cache()

	def on_trash(self):
		clear_store_cache()

	def before_rename(self, old: str, new: str, merge: bool = False) -> str:
		"""The code is in order and invoice numbers: changeable only until the first invoice."""
		if merge:
			frappe.throw(_("Stores cannot be merged"))
		new = (new or "").strip().upper()
		if not CODE_PATTERN.match(new):
			frappe.throw(_("Store Code must be 2 to 4 letters or digits"))
		if frappe.db.exists("Field Order", {"store": old, "invoice_no": ("is", "set")}):
			frappe.throw(_("Store {0} has issued invoices, so its code can no longer change").format(old))
		return new

	def after_rename(self, old: str, new: str, merge: bool = False) -> None:
		self.db_set("store_code", new, update_modified=False)
		clear_store_cache()

	def _guard_vendor_fields(self):
		"""
		Store Admin may edit its Store, but not who embedded requests sign in as, nor the
		connection and app credentials: only ``can_change_connection()`` users (vendor staff; on a
		single-store site also its System Managers) change those. The install flow saves as
		Administrator; code paths write them with ``set_store_value``.
		"""
		if self.is_new() or can_change_connection():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		for field in VENDOR_ONLY_FIELDS:
			new, old = self.get(field), before.get(field)
			if field == "shopify_client_secret":
				changed = (bool(new) and not self.is_dummy_password(new)) or (not new and bool(old))
			else:
				changed = (new or None) != (old or None)
			if changed:
				frappe.throw(
					_("Only a System Manager can change {0}").format(_(self.meta.get_label(field))),
					frappe.PermissionError,
				)

	def _apply_vendor_pause(self):
		"""
		``suspended`` (Paused by Support) keeps the store disabled until vendor staff clear it.

		Automatic disables (uninstall, shop/redact, a failed token refresh, a reset) write
		``enabled`` directly and never pass here, so a reinstall or token exchange may turn those
		stores back on (``oauth.save_installation``). A user who unticks Enabled on this form with
		``can_change_connection()`` (vendor staff; a single-store site's System Manager) pauses the
		store too, so opening the app from Shopify does not undo their disable. While paused, every
		save, the install's included, leaves it disabled.
		"""
		before = None if self.is_new() else self.get_doc_before_save()
		turned_off = bool(before and before.enabled and not self.enabled)
		if turned_off and not self.flags.from_store_install and can_change_connection():
			self.suspended = 1
		if self.suspended:
			self.enabled = 0

	def _validate_embedded_user(self):
		"""
		Embedded requests run as this user: never Administrator, Guest, vendor staff (they see every
		store) or another store's user (``can_be_embedded_user``).
		"""
		user = self.embedded_user
		if user and not can_be_embedded_user(user, self.name):
			frappe.throw(_("{0} cannot be this store's Embedded User").format(user))

	def _validate_store_code(self):
		self.store_code = (self.store_code or "").strip().upper()
		if not self.is_new() and self.store_code != self.name:
			frappe.throw(_("Rename the store to change its code"))
		if not CODE_PATTERN.match(self.store_code):
			frappe.throw(_("Store Code must be 2 to 4 letters or digits"))

	def _remember_tax_mode_choice(self):
		"""
		A tax mode changed through the Store (Settings, Desk) is the merchant's choice: from then
		on the shop's country no longer sets it (``sync/shop_settings.py``, which writes the Store
		directly and never passes here).
		"""
		if not self.is_new() and self.has_value_changed("tax_mode"):
			self.tax_mode_chosen = 1

	def _validate_order_mode(self):
		"""The shared App Store site sends orders as drafts only (paid on Shopify checkout)."""
		if is_multi_store() and self.order_mode == "Confirmed":
			frappe.throw(_("Order Mode must be Draft: orders are paid on the store's checkout"))

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
		# On the shared site each store always learns its own (the flag only means "copy into the site").
		if not (self.enabled and self.platform == "Shopify" and (self.match_shop_settings or is_multi_store())):
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
		enqueue_in_store(
			"field_sales.sync.shop_settings.scheduled_sync",
			store=self.name,
			queue="short",
			enqueue_after_commit=True,
			job_id=f"field_sales:shop_settings_sync:{self.name}",
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
		if changed and (
			before.get("access_token") or before.get("token_expiry") or before.get("refresh_token")
		):
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
