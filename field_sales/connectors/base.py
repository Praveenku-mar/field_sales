# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Platform-agnostic connector contract.

Core code (sync jobs, doc events, API endpoints) talks to a store only through
an instance returned by ``field_sales.connectors.get_connector()``. Every
platform connector subclasses ``BaseConnector`` and returns data in the
normalized shapes documented below, so callers never branch on platform.

IDs are plain platform ids (e.g. Shopify's numeric id, not its ``gid://`` form)
so they are safe in document names; each connector converts them back as needed.

Normalized product dict (one per *variant*)::

	{
		"external_product_id": str,
		"external_variant_id": str,
		"external_inventory_id": str | None,
		"product_title": str,
		"variant_title": str | None,
		"sku": str | None,
		"barcode": str | None,
		"price": float,
		"compare_at_price": float | None,
		"collection": str | None,
		"image_url": str | None,
		"status": "Active" | "Draft" | "Archived",
		"track_inventory": bool,
	}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from field_sales.field_sales.doctype.sync_log.sync_log import log_sync_durable

if TYPE_CHECKING:
	from frappe.model.document import Document


NORMALIZED_PRODUCT_KEYS: tuple[str, ...] = (
	"external_product_id",
	"external_variant_id",
	"external_inventory_id",
	"product_title",
	"variant_title",
	"sku",
	"barcode",
	"price",
	"compare_at_price",
	"collection",
	"image_url",
	"status",
	"track_inventory",
)


class ConnectorError(Exception):
	"""
	Raised by connectors for any failure talking to the store platform.

	:param message: Human-readable error, safe to show to the user.
	:param code: Short machine-readable code, e.g. ``"HTTP_ERROR"``,
		``"GRAPHQL_ERROR"``, ``"USER_ERROR"``, ``"AUTH_FAILED"``, ``"NETWORK_ERROR"``.
	:param http_status: HTTP status of the failing response, if there was one.
	:param details: Platform error entries behind the message (e.g. Shopify
		``userErrors`` as ``[{"field": [...], "message": str}]``), for callers
		that need to react to a specific field.
	:param partial: What was created on the store before the failure, so a
		retry can resume instead of duplicating it (e.g. ``{"draft_id": str}``).
	"""

	def __init__(
		self,
		message: str,
		code: str | None = None,
		http_status: int | None = None,
		details: list[dict] | None = None,
		partial: dict | None = None,
	):
		super().__init__(message)
		self.message = message
		self.code = code
		self.http_status = http_status
		self.details = details or []
		self.partial = partial or {}

	def __str__(self) -> str:
		return self.message


class BaseConnector:
	"""
	Abstract base for store connectors. Subclasses override every public method.

	:param settings: The ``Store Connection`` single document.
	"""

	#: Value written to ``Sync Log.platform``; subclasses set it.
	platform: str = ""

	def __init__(self, settings: Document):
		self.settings = settings

	def test_connection(self) -> dict:
		"""
		Verify credentials and reachability.

		:return: ``{"ok": bool, "shop_name": str, "message": str}``
		"""
		raise NotImplementedError

	def get_locations(self) -> list[dict]:
		"""
		List stock locations / warehouses on the store.

		:return: ``[{"id": str, "name": str}, ...]``
		"""
		raise NotImplementedError

	def get_products(self, cursor: str | None = None) -> dict:
		"""
		Fetch one page of products as normalized variant dicts.

		:param cursor: Opaque cursor from the previous page's ``next_cursor``; ``None`` for the first page.
		:return: ``{"items": [normalized product, ...], "next_cursor": str | None}``
		"""
		raise NotImplementedError

	def get_stock(self, inventory_ids: list, location_id: str) -> dict:
		"""
		Get available quantity per inventory item at one location.

		:param inventory_ids: ``external_inventory_id`` values from normalized products.
		:param location_id: Platform location id (``Store Connection.default_location_id``).
		:return: ``{inventory_id: qty}``; ids with no stock record at the location map to 0.
		"""
		raise NotImplementedError

	def create_customer(self, retailer: Document) -> str:
		"""
		Create the retailer as a customer on the store, or update it if it has
		an ``external_customer_id`` already.

		:param retailer: ``Retailer`` document.
		:return: The platform's customer id (stored as ``external_customer_id``).
		"""
		raise NotImplementedError

	def create_order(self, field_order: Document) -> dict:
		"""
		Push a submitted Field Order to the store.

		The retailer must already exist on the store (``external_customer_id``).
		If ``field_order.external_draft_id`` is set (a retry), that draft is
		reused instead of creating another. Whether the draft is confirmed into
		an order follows ``Store Connection.order_mode``.

		On failure after the draft was created, the ``ConnectorError`` carries
		``partial={"draft_id": str}``.

		:param field_order: ``Field Order`` document.
		:return: ``{"draft_id": str | None, "order_id": str | None, "order_name": str | None}``
		"""
		raise NotImplementedError

	def get_order_url(self, order_id: str | None, draft_id: str | None = None) -> str | None:
		"""
		Admin URL of an order on the store (or of its draft when not yet confirmed).

		:return: URL, or ``None`` if the platform has no such page.
		"""
		return None

	# ------------------------------------------------------------------
	# Shared helpers
	# ------------------------------------------------------------------

	def _write_log(self, operation: str, ref: Document | None = None, **fields) -> None:
		"""
		Write a Sync Log for one platform call; survives a rollback of the caller's transaction.

		:param operation: ``Sync Log.operation`` value.
		:param ref: Document the call was made for, if any.
		:param fields: Any other Sync Log fields (``status``, ``http_status``, ...).
		"""
		log_sync_durable(
			platform=self.platform,
			operation=operation,
			reference_doctype=ref.doctype if ref else None,
			reference_name=ref.name if ref else None,
			**fields,
		)
