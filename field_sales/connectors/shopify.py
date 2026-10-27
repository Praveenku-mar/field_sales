# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify connector (Admin GraphQL API).

Two auth modes (``Store Connection.shopify_auth_mode``):

- **Client Credentials** (stores in our own Shopify organization): the client
  id/secret on Store Connection are exchanged for a short-lived access token.
- **OAuth** (App Store installs): ``field_sales.shopify.oauth`` stores an
  expiring offline token and its refresh token; the app's credentials and API
  version come from site_config (``field_sales.shopify.config``).

Either way the access token is cached encrypted on Store Connection and renewed
shortly before it expires (or once on a 401).
"""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import timedelta

import frappe
import pycountry
import requests
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime
from frappe.utils.password import get_decrypted_password, remove_encrypted_password, set_encrypted_password

from field_sales.connectors.base import (
	ORDER_CANCELLED_TOPIC,
	ORDER_FULFILLED_TOPIC,
	ORDER_PAID_TOPIC,
	PRODUCT_DELETE_TOPIC,
	PRODUCT_UPDATE_TOPIC,
	REFUND_CREATE_TOPIC,
	STOCK_TOPIC,
	BaseConnector,
	ConnectorError,
)
from field_sales.shopify import config as app_config
from field_sales.utils.phone import to_e164

# (connect, read) seconds. A short connect limit means an unreachable address (e.g. a broken
# IPv6 route) is given up quickly and the next one (IPv4) is tried; replies still get 30 s.
REQUEST_TIMEOUT = (5, 30)
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
DEFAULT_TOKEN_TTL = 24 * 60 * 60  # used if Shopify omits expires_in
# Only one worker may refresh an OAuth token at a time (Shopify retires the other one's token).
TOKEN_LOCK_KEY = "field_sales:shopify_token_refresh"
TOKEN_LOCK_SECONDS = 30
THROTTLE_MIN_AVAILABLE = 100  # query cost points
THROTTLE_SLEEP_SECONDS = 2
PAGE_SIZE = 50
STOCK_BATCH_SIZE = 50

STATUS_MAP = {"ACTIVE": "Active", "DRAFT": "Draft", "ARCHIVED": "Archived"}

# Shopify topic -> neutral topic (see ``connectors.base``).
TOPIC_MAP = {
	"inventory_levels/update": STOCK_TOPIC,
	"products/update": PRODUCT_UPDATE_TOPIC,
	"products/delete": PRODUCT_DELETE_TOPIC,
	"orders/paid": ORDER_PAID_TOPIC,
	"orders/fulfilled": ORDER_FULFILLED_TOPIC,
	"orders/cancelled": ORDER_CANCELLED_TOPIC,
	"refunds/create": REFUND_CREATE_TOPIC,
}
PRODUCT_VARIANT_LIMIT = 250

# Fields of one variant node, shared by the full product sync and single-product fetch.
VARIANT_FIELDS = """
      id title sku barcode price compareAtPrice
      image { url }
      inventoryItem { id tracked }
      product { id title status productType featuredImage { url } collections(first: 1) { nodes { title } } }
"""

PRODUCTS_QUERY = """
query($cursor: String) {
  productVariants(first: %d, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { %s }
  }
}
""" % (PAGE_SIZE, VARIANT_FIELDS)

PRODUCT_QUERY = """
query($id: ID!) {
  product(id: $id) { variants(first: %d) { nodes { %s } } }
}
""" % (PRODUCT_VARIANT_LIMIT, VARIANT_FIELDS)

CUSTOMER_CREATE = """
mutation($input: CustomerInput!) {
  customerCreate(input: $input) { customer { id } userErrors { field message } }
}
"""

CUSTOMER_UPDATE = """
mutation($input: CustomerInput!) {
  customerUpdate(input: $input) { customer { id } userErrors { field message } }
}
"""

CUSTOMER_SEARCH = """
query($q: String!) { customers(first: 1, query: $q) { nodes { id } } }
"""

STOCK_QUERY = """
query($ids: [ID!]!, $loc: ID!) {
  nodes(ids: $ids) {
    ... on InventoryItem {
      id
      inventoryLevel(locationId: $loc) { quantities(names: ["available"]) { quantity } }
    }
  }
}
"""

DRAFT_ORDER_FIELDS = "id name status invoiceUrl order { id name }"

DRAFT_ORDER_CREATE = """
mutation($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) { draftOrder { %s } userErrors { field message } }
}
""" % DRAFT_ORDER_FIELDS

DRAFT_ORDER_QUERY = """
query($id: ID!) { draftOrder(id: $id) { %s } }
""" % DRAFT_ORDER_FIELDS

DRAFT_ORDERS_QUERY = """
query($ids: [ID!]!) { nodes(ids: $ids) { ... on DraftOrder { %s } } }
""" % DRAFT_ORDER_FIELDS

DRAFT_ORDER_DELETE = """
mutation($id: ID!) { draftOrderDelete(input: {id: $id}) { deletedId userErrors { field message } } }
"""

ORDER_CREATE = """
mutation($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
  orderCreate(order: $order, options: $options) { order { id name } userErrors { field message code } }
}
"""

ORDER_PAYMENT_STATE = """
query($id: ID!) { order(id: $id) { id name displayFinancialStatus canMarkAsPaid } }
"""

ORDER_MARK_AS_PAID = """
mutation($input: OrderMarkAsPaidInput!) {
  orderMarkAsPaid(input: $input) { order { id displayFinancialStatus } userErrors { field message } }
}
"""

# Financial statuses that already include the payment; marking again would fail.
PAID_FINANCIAL_STATUSES = ("PAID", "PARTIALLY_REFUNDED", "REFUNDED")

ORDER_SEARCH = """
query($q: String!) { orders(first: 1, query: $q) { nodes { id name } } }
"""

# App-owned order fields (Shopify shows the namespace as app--<app id>--field_sales). Staff can read
# them but not edit them, so the values stay trustworthy. They replace the old identifying tags.
METAFIELD_NAMESPACE = "$app:field_sales"
METAFIELD_TYPE = "single_line_text_field"
ORDER_METAFIELDS = {
	"field_order_no": ("Field Order No", "The Field Sales order this order was placed from."),
	"sales_rep": ("Sales Rep", "The sales rep who took the order: name and email."),
}
# Draft orders get the fields too; Shopify does not copy them when a draft is completed.
METAFIELD_OWNER_TYPES = ("ORDER", "DRAFTORDER")

METAFIELD_DEFINITION_CREATE = """
mutation($definition: MetafieldDefinitionInput!) {
  metafieldDefinitionCreate(definition: $definition) { createdDefinition { id } userErrors { field message code } }
}
"""

METAFIELDS_SET = """
mutation($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) { metafields { key } userErrors { field message code } }
}
"""

ORDER_LINE_LIMIT = 250
ORDER_REFUND_LIMIT = 50

# Everything commission needs about one order (amounts are shopMoney).
ORDER_QUERY = """
query($id: ID!) {
  order(id: $id) {
    id
    displayFinancialStatus
    displayFulfillmentStatus
    cancelledAt
    currencyCode
    tags
    lineItems(first: %d) {
      nodes {
        id
        quantity
        currentQuantity
        sku
        originalUnitPriceSet { shopMoney { amount } }
        discountAllocations { allocatedAmountSet { shopMoney { amount } } }
        variant { id }
        product { id productType tags }
      }
    }
    refunds(first: %d) {
      id
      createdAt
      refundLineItems(first: %d) {
        nodes { lineItem { id } quantity subtotalSet { shopMoney { amount } } }
      }
    }
  }
}
""" % (ORDER_LINE_LIMIT, ORDER_REFUND_LIMIT, ORDER_LINE_LIMIT)

# shopAddress, not billingAddress (deprecated in 2026-10); countryCodeV2, not countryCode (deprecated).
SHOP_INFO_QUERY = "{ shop { name myshopifyDomain currencyCode ianaTimezone shopAddress { countryCodeV2 } } }"
# Test Connection also reads the app's granted scopes.
TEST_CONNECTION_QUERY = (
	"{ shop { name myshopifyDomain currencyCode ianaTimezone shopAddress { countryCodeV2 } } "
	"currentAppInstallation { accessScopes { handle } } }"
)

WEBHOOK_SUBSCRIPTIONS_QUERY = """
query($topics: [WebhookSubscriptionTopic!]) {
  webhookSubscriptions(first: 50, topics: $topics) { nodes { id topic uri } }
}"""

WEBHOOK_SUBSCRIPTION_CREATE = """
mutation($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) {
    webhookSubscription { id topic uri }
    userErrors { field message }
  }
}"""

# Shopify needs a code for an order-level fixed discount; this one is shown on the order.
ORDER_DISCOUNT_CODE = "FIELD-ORDER"


class ShopifyConnector(BaseConnector):
	"""Connector for a single Shopify store configured on Store Connection."""

	platform = "Shopify"

	def __init__(self, settings):
		super().__init__(settings)
		self.shop_domain = normalize_shop_domain(settings.store_url)
		if not self.shop_domain:
			frappe.throw(_("Store URL is required on Store Connection"))
		if not is_myshopify_domain(self.shop_domain):
			frappe.throw(
				_("Store URL must be the shop's myshopify.com domain (e.g. your-store.myshopify.com), not {0}").format(
					self.shop_domain
				)
			)
		self.oauth = is_oauth(settings)
		api_version = app_config.api_version() if self.oauth else settings.shopify_api_version
		if not api_version:
			frappe.throw(_("Shopify API Version is required on Store Connection"))

		self.graphql_url = f"https://{self.shop_domain}/admin/api/{api_version}/graphql.json"
		self.token_url = f"https://{self.shop_domain}/admin/oauth/access_token"

		# Token cached for the life of this instance, so a refresh made here is
		# reused without re-reading Store Connection.
		self._token: str | None = None
		self._token_expiry = None
		# True once this instance has requested a token itself, so it carries
		# the store's current scopes and a scope error is not worth a retry.
		self._token_is_fresh = False
		# Shop currency (order prices are sent in it), looked up once per instance.
		self._currency: str | None = None

	@staticmethod
	def normalize_store_url(url: str | None) -> str:
		"""Store the bare ``*.myshopify.com`` domain, whatever form of URL was pasted."""
		if not (url or "").strip():
			return ""
		domain = normalize_shop_domain(url)
		if not is_myshopify_domain(domain):
			frappe.throw(
				_(
					"Store URL must be the shop's myshopify.com domain (e.g. your-store.myshopify.com). "
					"You can find it in Shopify admin under Settings > Domains."
				),
				title=_("Invalid Store URL"),
			)
		return domain

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------

	def test_connection(self) -> dict:
		"""Query the shop and the app's granted scopes; never raises for platform errors, returns ``ok=False`` instead."""
		try:
			data = self._graphql(TEST_CONNECTION_QUERY, operation="Test Connection")
		except ConnectorError as e:
			return {
				"ok": False,
				"shop_name": "",
				"shop_domain": self.shop_domain,
				"scopes": [],
				"currency": "",
				"timezone": "",
				"country": "",
				"message": e.message,
			}

		shop = data.get("shop") or {}
		scopes = ((data.get("currentAppInstallation") or {}).get("accessScopes")) or []
		if shop.get("currencyCode"):
			self._currency = shop["currencyCode"]
		return {
			"ok": True,
			"shop_name": shop.get("name") or "",
			"shop_domain": shop.get("myshopifyDomain") or self.shop_domain,
			"scopes": [s["handle"] for s in scopes if s.get("handle")],
			"currency": shop.get("currencyCode") or "",
			"timezone": shop.get("ianaTimezone") or "",
			"country": _shop_country(shop),
			"message": _("Connected to {0} ({1})").format(shop.get("name"), shop.get("myshopifyDomain")),
		}

	def get_shop_id(self) -> str:
		"""The shop's GID (``gid://shopify/Shop/<id>``), e.g. for the Partner API; cached a day in Redis."""
		key = frappe.cache.make_key(f"field_sales:shopify_shop_id:{self.shop_domain}")
		cached = frappe.cache.get(key)
		if cached:
			return cached.decode() if isinstance(cached, bytes) else cached
		shop_id = (self._graphql("{ shop { id } }", operation="Other").get("shop") or {}).get("id") or ""
		if shop_id:
			frappe.cache.set(key, shop_id, ex=24 * 60 * 60)
		return shop_id

	def ensure_webhook(self, topic: str, uri: str) -> str:
		"""
		Subscribe ``uri`` to ``topic`` (a ``WebhookSubscriptionTopic`` such as ``APP_UNINSTALLED``)
		unless that subscription exists. Shopify-only; used by ``field_sales.shopify.webhooks``.

		:return: ``"exists"`` or ``"created"``.
		"""
		data = self._graphql(WEBHOOK_SUBSCRIPTIONS_QUERY, {"topics": [topic]}, operation="Register Webhooks")
		nodes = (data.get("webhookSubscriptions") or {}).get("nodes") or []
		if any(n.get("uri") == uri for n in nodes):
			return "exists"
		self._graphql(
			WEBHOOK_SUBSCRIPTION_CREATE,
			{"topic": topic, "sub": {"uri": uri}},
			operation="Register Webhooks",
		)
		return "created"

	def ensure_order_metafields(self) -> bool:
		"""
		Create the Field Order No and Sales Rep field definitions on orders and draft orders.

		Safe to run again: a definition that already exists counts as done. Records the time on
		Store Connection (``metafields_ready_on``) once all of them exist, so it is not repeated.

		:return: ``True`` if every definition exists now; ``False`` if one could not be created
			(logged in Sync Log; orders still carry the values without a definition).
		"""
		ok = True
		for owner_type in METAFIELD_OWNER_TYPES:
			for key, (name, description) in ORDER_METAFIELDS.items():
				definition = {
					"name": name,
					"namespace": METAFIELD_NAMESPACE,
					"key": key,
					"type": METAFIELD_TYPE,
					"ownerType": owner_type,
					"description": description,
					"access": {"admin": "MERCHANT_READ"},
					"pin": True,
				}
				try:
					self._graphql(METAFIELD_DEFINITION_CREATE, {"definition": definition}, operation="Other")
				except ConnectorError as e:
					# TAKEN: it exists already (an earlier run, or the app was set up before).
					codes = {d.get("code") for d in (e.details or [])}
					if codes != {"TAKEN"}:
						ok = False
		if ok:
			frappe.db.set_single_value("Store Connection", "metafields_ready_on", now_datetime())
		return ok

	def set_order_metafields(self, order_id: str, field_order) -> None:
		"""Write the two fields onto an existing order (a completed draft does not inherit the draft's)."""
		owner = to_gid("Order", order_id)
		self._graphql(
			METAFIELDS_SET,
			{"metafields": [{**m, "ownerId": owner} for m in _order_metafields(field_order)]},
			operation="Confirm Order",
			ref=field_order,
		)

	def get_locations(self) -> list[dict]:
		"""Return the store's active locations as ``[{"id", "name"}]`` (ids are Shopify GIDs)."""
		data = self._graphql(
			"{ locations(first: 20) { nodes { id name isActive } } }", operation="Other"
		)
		nodes = (data.get("locations") or {}).get("nodes") or []
		return [{"id": from_gid(n["id"]), "name": n["name"]} for n in nodes if n.get("isActive")]

	def get_products(self, cursor: str | None = None) -> dict:
		"""Fetch one page of variants (``PAGE_SIZE`` per page) as normalized product dicts."""
		data = self._graphql(
			PRODUCTS_QUERY, {"cursor": cursor}, operation="Product Sync"
		)
		connection = data.get("productVariants") or {}
		page_info = connection.get("pageInfo") or {}
		return {
			"items": [_normalize_variant(v) for v in connection.get("nodes") or []],
			"next_cursor": page_info.get("endCursor") if page_info.get("hasNextPage") else None,
		}

	def get_stock(self, inventory_ids: list, location_id: str) -> dict:
		"""Available qty per inventory item at ``location_id``, queried in batches of ``STOCK_BATCH_SIZE``."""
		location_gid = to_gid("Location", location_id)
		stock = {}

		for start in range(0, len(inventory_ids), STOCK_BATCH_SIZE):
			batch = [str(i) for i in inventory_ids[start : start + STOCK_BATCH_SIZE]]
			data = self._graphql(
				STOCK_QUERY,
				{"ids": [to_gid("InventoryItem", i) for i in batch], "loc": location_gid},
				operation="Stock Check",
			)
			for node in data.get("nodes") or []:
				if not node or not node.get("id"):
					continue
				quantities = (node.get("inventoryLevel") or {}).get("quantities") or []
				stock[from_gid(node["id"])] = flt(quantities[0].get("quantity")) if quantities else 0.0

			# Items Shopify did not return (deleted, or not visible) count as out of stock.
			for i in batch:
				stock.setdefault(i, 0.0)

		return stock

	def get_product(self, external_product_id: str) -> list[dict]:
		"""Every variant of one product as normalized dicts; empty if the product was deleted."""
		data = self._graphql(
			PRODUCT_QUERY, {"id": to_gid("Product", external_product_id)}, operation="Product Sync"
		)
		product = data.get("product") or {}
		return [_normalize_variant(v) for v in (product.get("variants") or {}).get("nodes") or []]

	def verify_webhook(self, headers, raw_body: bytes) -> bool:
		"""HMAC-SHA256 of the body (keyed with the app's client secret) must match, from this shop."""
		signature = headers.get("X-Shopify-Hmac-Sha256")
		shop = headers.get("X-Shopify-Shop-Domain")
		if not signature or not shop:
			return False
		signature_ok = app_config.verify_body_hmac(raw_body, signature, [self._client_secret()])
		return signature_ok and shop.strip().lower() == self.shop_domain.lower()

	def parse_webhook(self, headers, raw_body: bytes) -> dict:
		"""Neutral event from Shopify's ``X-Shopify-*`` headers and JSON body."""
		platform_topic = headers.get("X-Shopify-Topic") or ""
		try:
			payload = json.loads(raw_body or b"{}")
		except ValueError:
			payload = {}
		if not isinstance(payload, dict):
			payload = {}

		return {
			"topic": TOPIC_MAP.get(platform_topic, platform_topic),
			"platform_topic": platform_topic,
			"event_id": headers.get("X-Shopify-Webhook-Id") or None,
			"updated_at": payload.get("updated_at"),
			"payload": payload,
		}

	def handle_stock_event(self, payload: dict) -> dict | None:
		"""``inventory_levels/update`` payload -> plain ids and quantity; ``None`` if ids are missing."""
		inventory_id = payload.get("inventory_item_id")
		location_id = payload.get("location_id")
		if not inventory_id or not location_id:
			return None

		return {
			"external_inventory_id": from_gid(str(inventory_id)),
			"location_id": from_gid(str(location_id)),
			"available": flt(payload.get("available")),
		}

	def handle_product_event(self, payload: dict) -> list[dict] | None:
		"""``products/update`` payload -> plain variant ids and prices; ``None`` if it has no variants."""
		variants = payload.get("variants")
		if not variants:
			return None

		prices = []
		for variant in variants:
			variant_id = variant.get("id") if isinstance(variant, dict) else None
			if not variant_id or variant.get("price") in (None, ""):
				raise ConnectorError(_("Product webhook has a variant without an id or price."), code="BAD_PAYLOAD")
			compare_at = variant.get("compare_at_price")
			prices.append(
				{
					"external_variant_id": from_gid(str(variant_id)),
					"price": flt(variant["price"]),
					"compare_at_price": flt(compare_at) if compare_at not in (None, "") else None,
				}
			)
		return prices

	def order_id_from_event(self, payload: dict) -> str | None:
		"""``orders/*`` carry the order as ``id``; ``refunds/create`` carries it as ``order_id``."""
		value = payload.get("order_id") or payload.get("id")
		return from_gid(str(value)) if value else None

	def get_order(self, external_id: str) -> dict:
		"""Current state of one order, normalized (see ``connectors.base``); empty if it is gone."""
		data = self._graphql(ORDER_QUERY, {"id": to_gid("Order", external_id)}, operation="Other")
		return _normalize_order(data.get("order"))

	def create_customer(self, retailer) -> dict:
		"""
		Create the retailer as a Shopify customer, or update the linked one.

		On create, a "has already been taken" error for the phone or email is
		resolved by linking the existing customer with that phone/email. If the
		linked customer no longer exists (deleted or merged on Shopify), it is
		created again the same way.

		If Shopify rejects the email itself (e.g. "invalid domain name"), the
		push is retried once without it and the result carries a warning.

		Addresses are only sent on create: how ``customerUpdate`` treats an
		``addresses`` list is undocumented, and resending an identical address
		has been reported to delete it.

		:return: ``{"id": plain Shopify customer id, "warning": str | None}``
		"""
		try:
			return {"id": self._push_customer(retailer, with_email=True), "warning": None}
		except ConnectorError as e:
			if not (retailer.email and _is_rejected_email(e)):
				raise

		return {
			"id": self._push_customer(retailer, with_email=False),
			"warning": _("Synced without email — email domain rejected by store"),
		}

	def _push_customer(self, retailer, with_email: bool) -> str:
		"""One create-or-update attempt; see ``create_customer``."""
		customer_input = _customer_input(retailer)
		if not with_email:
			customer_input.pop("email", None)

		if retailer.external_customer_id:
			try:
				data = self._graphql(
					CUSTOMER_UPDATE,
					{"input": {**customer_input, "id": to_gid("Customer", retailer.external_customer_id)}},
					operation="Update Customer",
					ref=retailer,
				)
				return from_gid(data["customerUpdate"]["customer"]["id"])
			except ConnectorError as e:
				# The linked customer was deleted or merged away on Shopify:
				# fall through and create (or re-link) it instead.
				if not _is_missing_customer(e):
					raise

		customer_input["addresses"] = [_customer_address(retailer, customer_input.get("phone"))]
		try:
			data = self._graphql(
				CUSTOMER_CREATE, {"input": customer_input}, operation="Create Customer", ref=retailer
			)
		except ConnectorError as e:
			existing_id = self._find_taken_customer(e, customer_input, retailer)
			if existing_id:
				return existing_id
			raise

		return from_gid(data["customerCreate"]["customer"]["id"])

	def _find_taken_customer(self, error: ConnectorError, customer_input: dict, retailer) -> str | None:
		"""Return the id of the customer that already holds the phone/email ``error`` complains about."""
		if error.code != "USER_ERROR":
			return None

		for user_error in error.details:
			field = (user_error.get("field") or [""])[-1]
			value = customer_input.get(field)
			if field in ("phone", "email") and value and "taken" in (user_error.get("message") or "").lower():
				data = self._graphql(
					CUSTOMER_SEARCH, {"q": f'{field}:"{value}"'}, operation="Create Customer", ref=retailer
				)
				nodes = (data.get("customers") or {}).get("nodes") or []
				if nodes:
					return from_gid(nodes[0]["id"])

		return None

	def create_order(self, field_order) -> dict:
		"""
		Push a submitted Field Order according to Store Connection's Order Mode.

		- Confirmed: a real order (``orderCreate``) with the Field Order's tax as
		  an explicit tax line and payment pending, so Shopify shows the same
		  subtotal, discount, tax and total.
		- Draft: a draft order. Drafts take no tax amount (Shopify only
		  estimates tax from its own settings), so the tax is added as a
		  non-taxable line item to keep the total equal.

		Retries never duplicate: a linked draft is reused (or its completed
		order returned), and an order already created for this Field Order is
		found by its source identifier (the Field Order name).

		The order (or draft) carries the Field Order No and Sales Rep fields
		(``ORDER_METAFIELDS``) and a single "field-sales" tag.

		:return: ``{"draft_id", "order_id", "order_name"}`` (plain ids; order fields ``None`` in Draft mode).
		"""
		if not self.settings.metafields_ready_on:
			# First order on this store (or Test Connection never ran): set up the order fields.
			# A failure is logged and does not block the order; the values are sent anyway.
			self.ensure_order_metafields()

		retailer = frappe.get_doc("Retailer", field_order.retailer)
		if not retailer.external_customer_id:
			raise ConnectorError(
				_("Retailer {0} is not linked to a Shopify customer yet").format(retailer.name),
				code="CUSTOMER_MISSING",
			)

		draft = self._get_draft(field_order.external_draft_id, field_order) if field_order.external_draft_id else None
		if draft and draft.get("status") == "COMPLETED":
			return _order_result(draft)

		if self.settings.order_mode != "Confirmed":
			return _order_result(draft or self._create_draft(field_order, retailer))

		order = self._find_order(field_order) or self._create_confirmed_order(field_order, retailer)
		if draft:
			# Left over from an earlier push in Draft mode; the order replaces it.
			self._delete_draft(draft["id"], field_order)
		return {"draft_id": None, "order_id": from_gid(order["id"]), "order_name": order.get("name")}

	def get_completed_drafts(self, draft_ids: list) -> dict:
		"""``{draft_id: order_id}`` for drafts completed on Shopify, queried in batches of ``PAGE_SIZE``."""
		completed = {}
		for start in range(0, len(draft_ids), PAGE_SIZE):
			batch = draft_ids[start : start + PAGE_SIZE]
			data = self._graphql(
				DRAFT_ORDERS_QUERY,
				{"ids": [to_gid("DraftOrder", i) for i in batch]},
				operation="Confirm Order",
			)
			for node in data.get("nodes") or []:
				# Deleted drafts come back as null.
				if node and node.get("status") == "COMPLETED" and node.get("order"):
					completed[from_gid(node["id"])] = from_gid(node["order"]["id"])
		return completed

	def get_draft_names(self, draft_ids: list) -> dict:
		"""``{draft_id: name}`` (e.g. ``#D5``) for drafts that still exist, in batches of ``PAGE_SIZE``."""
		names = {}
		for start in range(0, len(draft_ids), PAGE_SIZE):
			batch = draft_ids[start : start + PAGE_SIZE]
			data = self._graphql(DRAFT_ORDERS_QUERY, {"ids": [to_gid("DraftOrder", i) for i in batch]}, operation="Other")
			for node in data.get("nodes") or []:
				if node and node.get("name"):
					names[from_gid(node["id"])] = node["name"]
		return names

	def get_order_url(self, order_id: str | None, draft_id: str | None = None) -> str | None:
		if order_id:
			return f"https://{self.shop_domain}/admin/orders/{from_gid(order_id)}"
		if draft_id:
			return f"https://{self.shop_domain}/admin/draft_orders/{from_gid(draft_id)}"
		return None

	def _create_confirmed_order(self, field_order, retailer) -> dict:
		"""``orderCreate`` with explicit tax, discount and payment pending; returns ``{"id", "name"}``."""
		currency = self._get_currency(field_order)
		address = _customer_address(retailer, retailer_phone(retailer))

		order_input = {
			"customer": {"toAssociate": {"id": to_gid("Customer", retailer.external_customer_id)}},
			"lineItems": [_order_line_item(row, currency) for row in field_order.items],
			"taxesIncluded": False,
			"financialStatus": "PENDING",
			"note": _order_note(field_order),
			"tags": _order_tags(field_order),
			"metafields": _order_metafields(field_order),
			"sourceIdentifier": field_order.name,
			"shippingAddress": address,
			"billingAddress": address,
		}

		tax_amount = flt(field_order.tax_amount, 2)
		if tax_amount > 0:
			# Order-level, so the amount is exactly the Field Order's (per-line tax could round differently).
			order_input["taxLines"] = [
				{
					"title": _("Tax"),
					"rate": str(flt(field_order.tax_percent) / 100),
					"priceSet": _money(tax_amount, currency),
				}
			]

		if flt(field_order.discount_amount) > 0:
			order_input["discountCode"] = {
				"itemFixedDiscountCode": {
					"code": ORDER_DISCOUNT_CODE,
					"amountSet": _money(field_order.discount_amount, currency),
				}
			}

		data = self._graphql(
			ORDER_CREATE,
			{
				"order": order_input,
				"options": {
					"inventoryBehaviour": "DECREMENT_OBEYING_POLICY",
					"sendReceipt": False,
					"sendFulfillmentReceipt": False,
				},
			},
			operation="Confirm Order",
			ref=field_order,
		)
		return data["orderCreate"]["order"]

	def _find_order(self, field_order) -> dict | None:
		"""An order already created for this Field Order (a retry after a lost response)."""
		# Orders from older versions were found by tag; still look for one, so their retries never duplicate.
		for query in (f'source_identifier:"{field_order.name}"', f'tag:"{_legacy_field_order_tag(field_order)}"'):
			data = self._graphql(ORDER_SEARCH, {"q": query}, operation="Confirm Order", ref=field_order)
			nodes = (data.get("orders") or {}).get("nodes") or []
			if nodes:
				return nodes[0]
		return None

	def mark_order_paid(self, field_order) -> dict:
		"""``orderMarkAsPaid`` for a confirmed order; checks ``canMarkAsPaid`` first so a repeat is a no-op."""
		gid = to_gid("Order", field_order.external_order_id)
		data = self._graphql(ORDER_PAYMENT_STATE, {"id": gid}, operation="Mark Order Paid", ref=field_order)
		order = data.get("order")
		if not order:
			raise ConnectorError(
				_("Shopify order {0} was not found").format(field_order.external_order_id), code="NOT_FOUND"
			)
		status = order.get("displayFinancialStatus") or ""
		if status in PAID_FINANCIAL_STATUSES:
			return {"financial_status": status.lower(), "already_paid": True}
		if not order.get("canMarkAsPaid"):
			raise ConnectorError(
				_("Shopify cannot mark order {0} as paid (payment status {1})").format(
					order.get("name") or field_order.external_order_id, status.lower() or "unknown"
				),
				code="CANNOT_MARK_PAID",
			)
		data = self._graphql(ORDER_MARK_AS_PAID, {"input": {"id": gid}}, operation="Mark Order Paid", ref=field_order)
		paid = (data.get("orderMarkAsPaid") or {}).get("order") or {}
		return {"financial_status": (paid.get("displayFinancialStatus") or "").lower(), "already_paid": False}

	def _get_draft(self, draft_id: str, field_order) -> dict | None:
		"""The linked draft order, or ``None`` if it no longer exists on Shopify."""
		data = self._graphql(
			DRAFT_ORDER_QUERY,
			{"id": to_gid("DraftOrder", draft_id)},
			operation="Create Order",
			ref=field_order,
		)
		return data.get("draftOrder")

	def _delete_draft(self, draft_gid: str, field_order) -> None:
		"""Best effort: a stale draft left behind is harmless, so a failure here is only logged."""
		try:
			self._graphql(DRAFT_ORDER_DELETE, {"id": draft_gid}, operation="Other", ref=field_order)
		except ConnectorError:
			pass

	def _create_draft(self, field_order, retailer) -> dict:
		currency = self._get_currency(field_order)
		address = _customer_address(retailer, retailer_phone(retailer))

		line_items = [_draft_line_item(row, currency) for row in field_order.items]
		tax_line = _tax_line_item(field_order, currency)
		if tax_line:
			line_items.append(tax_line)

		draft_input = {
			"purchasingEntity": {"customerId": to_gid("Customer", retailer.external_customer_id)},
			"lineItems": line_items,
			# Tax comes from the Field Order (as its own line); Shopify must not add its own on top.
			"taxExempt": True,
			"note": _order_note(field_order),
			"tags": _order_tags(field_order),
			"metafields": _order_metafields(field_order),
			"shippingAddress": address,
			"billingAddress": address,
		}
		if flt(field_order.discount_amount) > 0:
			draft_input["appliedDiscount"] = {
				"valueType": "FIXED_AMOUNT",
				"value": flt(field_order.discount_amount),
				"title": _("Field Order discount"),
			}

		data = self._graphql(
			DRAFT_ORDER_CREATE, {"input": draft_input}, operation="Create Order", ref=field_order
		)
		return data["draftOrderCreate"]["draftOrder"]

	def get_shop_settings(self) -> dict:
		"""The shop's currency, IANA time zone and country (raises ``ConnectorError``)."""
		data = self._graphql(SHOP_INFO_QUERY, operation="Test Connection")
		shop = data.get("shop") or {}
		if shop.get("currencyCode"):
			self._currency = shop["currencyCode"]
		return {
			"currency": shop.get("currencyCode") or "",
			"timezone": shop.get("ianaTimezone") or "",
			"country": _shop_country(shop),
		}

	def _get_currency(self, field_order) -> str:
		"""
		The shop's currency; order prices are sent in it without conversion.

		Raises ``ConnectorError(code="CURRENCY_MISMATCH")`` when the site's default currency is
		different, so an order is never sent with prices in the wrong currency.
		"""
		if self._currency is None:
			data = self._graphql(SHOP_INFO_QUERY, operation="Create Order", ref=field_order)
			self._currency = (data.get("shop") or {}).get("currencyCode")
		site_currency = frappe.db.get_default("currency")
		if self._currency and site_currency and self._currency != site_currency:
			raise ConnectorError(
				_(
					"The store's currency is {0} but this site uses {1}. Order prices would be sent in the "
					"wrong currency. Turn on Match Store Currency and Timezone on Store Connection (or set "
					"the site currency to {0}), then retry."
				).format(self._currency, site_currency),
				code="CURRENCY_MISMATCH",
			)
		return self._currency

	# ------------------------------------------------------------------
	# Auth
	# ------------------------------------------------------------------

	def _client_id(self) -> str:
		"""The app's client id: site_config in OAuth mode, Store Connection otherwise."""
		return app_config.client_id() if self.oauth else (self.settings.shopify_client_id or "")

	def _client_secret(self) -> str:
		"""The app's client secret (also the webhook signing key); never logged."""
		if self.oauth:
			return app_config.client_secret()
		return self.settings.get_password("shopify_client_secret", raise_exception=False) or ""

	def _get_token(self, force_refresh: bool = False) -> str:
		"""
		Return a valid access token, requesting a new one if needed.

		The stored token is reused while it has more than ``TOKEN_REFRESH_MARGIN``
		left (an OAuth token without an expiry is non-expiring). ``force_refresh``
		skips that check (used after a 401).
		"""
		if not force_refresh:
			if self._token is None:
				self._token = self.settings.get_password("access_token", raise_exception=False)
				self._token_expiry = self.settings.token_expiry

			if self._token and (
				(self._token_expiry and get_datetime(self._token_expiry) > now_datetime() + TOKEN_REFRESH_MARGIN)
				or (self.oauth and not self._token_expiry)
			):
				return self._token

		if self.oauth:
			return self._refresh_oauth_token(stale_token=self._token)
		return self._request_new_token()

	def _request_new_token(self) -> str:
		"""Run the client-credentials grant, store the token encrypted, and commit."""
		client_id = self._client_id()
		client_secret = self._client_secret()
		if not client_id or not client_secret:
			raise ConnectorError(
				_("Shopify Client ID and Client Secret are required on Store Connection"), code="CONFIG_ERROR"
			)

		# The secret is never logged.
		log_request = frappe.as_json({"grant_type": "client_credentials", "client_id": client_id})
		start = time.monotonic()
		try:
			response = requests.post(
				self.token_url,
				data={
					"grant_type": "client_credentials",
					"client_id": client_id,
					"client_secret": client_secret,
				},
				headers={"Accept": "application/json"},
				timeout=REQUEST_TIMEOUT,
			)
		except requests.RequestException as e:
			self._log_token_call("Failed", start, None, log_request, None, str(e), "NETWORK_ERROR")
			raise ConnectorError(
				_("Could not reach Shopify: {0}").format(e), code="NETWORK_ERROR"
			) from e

		body = _json_or_none(response)
		token = (body or {}).get("access_token")
		if not response.ok or not token:
			message = _error_text(body, response) or _("No access token in response")
			self._log_token_call(
				"Failed", start, response.status_code, log_request, response.text, message, "AUTH_FAILED"
			)
			raise ConnectorError(
				_("Shopify authentication failed: {0}").format(message),
				code="AUTH_FAILED",
				http_status=response.status_code,
			)

		self._log_token_call("Success", start, response.status_code, log_request, response.text)

		expiry = add_to_date(now_datetime(), seconds=cint(body.get("expires_in")) or DEFAULT_TOKEN_TTL)
		# Password fields keep the real value in __Auth and a mask in the field
		# itself; writing the token with set_single_value alone would store it
		# in plain text, and the next form save would wipe the stored token.
		set_encrypted_password("Store Connection", "Store Connection", token, "access_token")
		frappe.db.set_single_value(
			"Store Connection", {"access_token": "*" * len(token), "token_expiry": expiry}
		)
		frappe.db.commit()

		self._token, self._token_expiry = token, expiry
		self._token_is_fresh = True
		return token

	def _refresh_oauth_token(self, stale_token: str | None = None) -> str:
		"""
		OAuth mode: swap the stored refresh token for a new access + refresh token, store both, commit.

		Runs under a Redis lock: Shopify retires the other token whenever one is issued, so two
		workers refreshing at once would invalidate each other. A worker that waited for the lock
		uses the token the other one stored instead of refreshing again.

		A missing, expired or rejected refresh token disables Store Connection and raises
		``ConnectorError(code="REAUTH_REQUIRED")``: the merchant must open the app from Shopify again.

		:param stale_token: The token this instance already holds (and wants replaced).
		"""
		lock = frappe.cache.lock(
			frappe.cache.make_key(TOKEN_LOCK_KEY), timeout=TOKEN_LOCK_SECONDS, blocking_timeout=TOKEN_LOCK_SECONDS
		)
		if not lock.acquire():
			raise ConnectorError(_("Timed out waiting for another Shopify token refresh"), code="AUTH_FAILED")
		try:
			# End this transaction's snapshot so the reads below see a token another worker committed.
			frappe.db.commit()
			current = get_decrypted_password(
				"Store Connection", "Store Connection", "access_token", raise_exception=False
			)
			expiry = _stored_datetime("token_expiry")
			if (
				current
				and current != stale_token
				and expiry
				and expiry > now_datetime() + TOKEN_REFRESH_MARGIN
			):
				self._token, self._token_expiry = current, expiry
				return current

			refresh_token = get_decrypted_password(
				"Store Connection", "Store Connection", "refresh_token", raise_exception=False
			)
			refresh_expiry = _stored_datetime("refresh_token_expiry")
			if not refresh_token:
				self._require_reauth(_("No Shopify refresh token is stored"))
			if refresh_expiry and refresh_expiry <= now_datetime():
				self._require_reauth(_("The Shopify refresh token expired on {0}").format(refresh_expiry))

			client_id, client_secret = self._client_id(), self._client_secret()
			if not client_id or not client_secret:
				raise ConnectorError(
					_("shopify_app_client_id and shopify_app_client_secret must be set in site_config"),
					code="CONFIG_ERROR",
				)

			log_request = frappe.as_json({"grant_type": "refresh_token", "client_id": client_id})
			start = time.monotonic()
			try:
				response = requests.post(
					self.token_url,
					data={
						"client_id": client_id,
						"client_secret": client_secret,
						"grant_type": "refresh_token",
						"refresh_token": refresh_token,
					},
					headers={"Accept": "application/json"},
					timeout=REQUEST_TIMEOUT,
				)
			except requests.RequestException as e:
				self._log_token_call("Failed", start, None, log_request, None, str(e), "NETWORK_ERROR")
				raise ConnectorError(_("Could not reach Shopify: {0}").format(e), code="NETWORK_ERROR") from e

			body = _json_or_none(response) or {}
			if response.status_code in (400, 401) or (response.ok and not body.get("access_token")):
				message = _error_text(body, response) or _("No access token in response")
				self._log_token_call(
					"Failed", start, response.status_code, log_request, response.text, message, "REAUTH_REQUIRED"
				)
				self._require_reauth(_("Shopify rejected the refresh token: {0}").format(message))
			if not response.ok:
				message = _error_text(body, response)
				self._log_token_call(
					"Failed", start, response.status_code, log_request, response.text, message, "AUTH_FAILED"
				)
				raise ConnectorError(
					_("Shopify token refresh failed: {0}").format(message),
					code="AUTH_FAILED",
					http_status=response.status_code,
				)

			self._log_token_call("Success", start, response.status_code, log_request, response.text)
			expiry = save_oauth_tokens(body)
			frappe.db.commit()
		finally:
			try:
				lock.release()
			except Exception:
				pass  # the lock timed out and was taken over; nothing to release

		self._token, self._token_expiry = body["access_token"], expiry
		self._token_is_fresh = True
		return self._token

	def _require_reauth(self, reason: str):
		"""Disable the connection, drop the dead tokens, and raise ``REAUTH_REQUIRED``."""
		message = _("Reinstall required: {0}. Open the app from Shopify admin to reconnect.").format(reason)
		clear_oauth_tokens()
		frappe.db.set_single_value(
			"Store Connection", {"enabled": 0, "last_test_status": message[:140], "last_tested_on": now_datetime()}
		)
		frappe.db.commit()
		self._write_log("Token Refresh", status="Failed", error_code="REAUTH_REQUIRED", error_message=message)
		raise ConnectorError(message, code="REAUTH_REQUIRED")

	def _log_token_call(self, status, start, http_status, request, response, error=None, code=None):
		"""Write the Sync Log for one token request."""
		self._write_log(
			"Token Refresh",
			status=status,
			endpoint=self.token_url,
			http_status=http_status,
			duration_ms=_elapsed_ms(start),
			request_payload=request,
			response_payload=response,
			error_message=error,
			error_code=code,
		)

	# ------------------------------------------------------------------
	# GraphQL transport
	# ------------------------------------------------------------------

	def _graphql(self, query: str, variables: dict | None = None, operation: str = "Other", ref=None) -> dict:
		"""
		Run a GraphQL query/mutation and return its ``data``.

		Retries once with a fresh token on 401, or on ACCESS_DENIED when the
		token was not issued during this call (it may predate newly granted
		scopes). Raises ``ConnectorError`` on
		network/HTTP errors, top-level ``errors`` or any non-empty ``userErrors``.
		Every attempt writes a Sync Log.

		:param operation: ``Sync Log.operation`` value for the log entry.
		:param ref: Document the call is made for (stored as the log's reference).
		"""
		payload = {"query": query, "variables": variables or {}}
		request_json = frappe.as_json(payload)

		for attempt in (1, 2):
			token = self._get_token(force_refresh=attempt > 1)
			log = {"operation": operation, "ref": ref, "attempt": attempt, "endpoint": self.graphql_url}

			start = time.monotonic()
			try:
				response = requests.post(
					self.graphql_url,
					json=payload,
					headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
					timeout=REQUEST_TIMEOUT,
				)
			except requests.RequestException as e:
				self._write_log(
					**log,
					status="Failed",
					duration_ms=_elapsed_ms(start),
					request_payload=request_json,
					error_message=str(e),
					error_code="NETWORK_ERROR",
				)
				raise ConnectorError(
					_("Could not reach Shopify: {0}").format(e), code="NETWORK_ERROR"
				) from e

			log.update(
				duration_ms=_elapsed_ms(start),
				http_status=response.status_code,
				request_payload=request_json,
				response_payload=response.text,
			)

			if response.status_code == 401 and attempt == 1:
				self._write_log(
					**log,
					status="Retrying",
					error_message="Unauthorized; refreshing token",
					error_code="HTTP_401",
				)
				continue

			body = _json_or_none(response)
			error = _graphql_error(response, body)

			# A token only carries the scopes granted when it was issued; if the
			# store has since accepted more, a fresh token picks them up.
			# In OAuth mode scopes only change by reinstalling, so a refresh would not help.
			if (
				error
				and error.code == "ACCESS_DENIED"
				and attempt == 1
				and not self._token_is_fresh
				and not self.oauth
			):
				self._write_log(**log, status="Retrying", error_message=error.message, error_code=error.code)
				continue

			if error:
				self._write_log(**log, status="Failed", error_message=error.message, error_code=error.code)
				raise error

			self._write_log(**log, status="Success")
			_respect_throttle(body)
			return body.get("data") or {}


# ----------------------------------------------------------------------
# Module helpers
# ----------------------------------------------------------------------


def to_gid(kind: str, plain_id: str | int) -> str:
	"""``to_gid("Location", 123)`` -> ``gid://shopify/Location/123``; GIDs pass through unchanged."""
	plain_id = str(plain_id)
	return plain_id if plain_id.startswith("gid://") else f"gid://shopify/{kind}/{plain_id}"


def from_gid(gid: str | None) -> str | None:
	"""``gid://shopify/ProductVariant/123`` -> ``123``."""
	return gid.rsplit("/", 1)[-1] if gid else None


def _shop_country(shop: dict) -> str:
	"""The shop's ISO country code from ``shop { shopAddress { countryCodeV2 } }``, or ``""``."""
	return ((shop.get("shopAddress") or {}).get("countryCodeV2") or "").upper()


def retailer_country_code(retailer) -> str:
	"""The retailer's ISO country code (``Country.code``, upper case), or ``""`` if unknown."""
	country = retailer.get("country") if hasattr(retailer, "get") else None
	if not country:
		return ""
	return (frappe.db.get_value("Country", country, "code") or "").upper()


def retailer_phone(retailer) -> str | None:
	"""The retailer's phone in E.164 for its country, or ``None`` if not a valid number."""
	return to_e164(retailer.phone, retailer_country_code(retailer))


# Countries whose Shopify province codes are the ISO 3166-2 ones. Elsewhere (e.g. GB, AE) Shopify
# has no provinces or its own codes, so the state is sent as text.
PROVINCE_CODE_COUNTRIES = {"IN", "US", "CA", "AU"}
# ISO codes Shopify spells differently: send the text for these.
PROVINCE_CODE_SKIP = {("IN", "TG")}  # Telangana: Shopify uses TS; newer ISO data says TG


def province_code(country_code: str, state: str | None) -> str | None:
	"""
	Shopify ``provinceCode`` for ``state`` (a name like "Tamil Nadu" or a code like "TN"), when the
	country uses ISO codes in Shopify and the state matches exactly one ISO 3166-2 subdivision;
	else ``None`` (the caller sends the text instead).
	"""
	country_code = (country_code or "").upper()
	state = (state or "").strip()
	if country_code not in PROVINCE_CODE_COUNTRIES or not state:
		return None
	wanted = _plain(state)
	matches = {
		sub.code.split("-", 1)[1]
		for sub in pycountry.subdivisions.get(country_code=country_code) or []
		if _plain(sub.name) == wanted or _plain(sub.code.split("-", 1)[1]) == wanted
	}
	if len(matches) != 1:
		return None
	code = matches.pop()
	return None if (country_code, code) in PROVINCE_CODE_SKIP else code


def _plain(text: str) -> str:
	"""Case- and accent-insensitive form ("Tamil Nādu" == "tamil nadu")."""
	decomposed = unicodedata.normalize("NFKD", text)
	return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def _is_missing_customer(error: ConnectorError) -> bool:
	"""True if ``error`` is Shopify saying the customer id sent does not exist."""
	return error.code == "USER_ERROR" and any(
		(e.get("field") or [""])[-1] == "id" and "does not exist" in (e.get("message") or "").lower()
		for e in error.details
	)


def _is_rejected_email(error: ConnectorError) -> bool:
	"""
	True if Shopify refused the email address itself (e.g. "invalid domain name").

	"Has already been taken" is not a rejection: it is resolved by linking the
	existing customer, so it never reaches here unless that lookup failed.
	"""
	return error.code == "USER_ERROR" and any(
		(e.get("field") or [""])[-1] == "email" and "taken" not in (e.get("message") or "").lower()
		for e in error.details
	)


def _customer_input(retailer) -> dict:
	"""Retailer -> CustomerInput, without ``id`` or ``addresses``."""
	customer_input = {
		"firstName": retailer.owner_name or retailer.retailer_name,
		"lastName": "",
		"note": f"Field Sales retailer {retailer.name} \u00b7 {retailer.retailer_name}",
		"tags": [t for t in ("field-sales", retailer.retailer_type, retailer.city) if t],
	}
	if retailer.email:
		customer_input["email"] = retailer.email.strip()
	phone = retailer_phone(retailer)
	if phone:
		customer_input["phone"] = phone
	return customer_input


def _customer_address(retailer, phone: str | None) -> dict:
	"""
	Retailer address -> MailingAddressInput, in the retailer's country.

	The state goes as ``provinceCode`` when it maps to one subdivision of that country,
	else as ``province`` text (deprecated but still accepted).
	"""
	country_code = retailer_country_code(retailer)
	address = {
		"address1": retailer.address_line1 or "",
		"address2": retailer.address_line2 or "",
		"city": retailer.city or "",
		"zip": retailer.pincode or "",
		"company": retailer.retailer_name,
	}
	if country_code:
		address["countryCode"] = country_code
	code = province_code(country_code, retailer.state)
	if code:
		address["provinceCode"] = code
	elif retailer.state:
		address["province"] = retailer.state
	if phone:
		address["phone"] = phone
	return address


def _draft_line_item(row, currency: str) -> dict:
	"""Field Order Item -> DraftOrderLineItemInput, at the order's rate."""
	_check_line_item(row)
	return {
		"variantId": to_gid("ProductVariant", row.external_variant_id),
		"quantity": cint(row.qty),
		"priceOverride": {"amount": str(flt(row.rate, 2)), "currencyCode": currency},
	}


def _order_line_item(row, currency: str) -> dict:
	"""Field Order Item -> OrderCreateLineItemInput, at the order's rate."""
	_check_line_item(row)
	return {
		"variantId": to_gid("ProductVariant", row.external_variant_id),
		"quantity": cint(row.qty),
		"priceSet": _money(row.rate, currency),
		"taxable": True,
	}


def _check_line_item(row) -> None:
	if not row.external_variant_id:
		raise ConnectorError(
			_("Row {0}: {1} is not linked to a Shopify variant").format(row.idx, row.product_title or row.product),
			code="CONFIG_ERROR",
		)
	if flt(row.qty) != cint(row.qty):
		raise ConnectorError(
			_("Row {0}: Shopify only accepts whole quantities, not {1}").format(row.idx, flt(row.qty)),
			code="CONFIG_ERROR",
		)


def _money(amount, currency: str) -> dict:
	"""MoneyBagInput in the shop currency."""
	return {"shopMoney": {"amount": str(flt(amount, 2)), "currencyCode": currency}}


def _legacy_field_order_tag(field_order) -> str:
	"""Tag older versions put on the order to find it again on retry (no longer added)."""
	return f"field-order-{field_order.name}"


def _order_tags(field_order) -> list[str]:
	"""One tag so staff can filter Field Sales orders; the order number and rep are metafields."""
	return ["field-sales"]


def _order_metafields(field_order) -> list[dict]:
	"""MetafieldInput values for the Field Order No and Sales Rep fields."""
	full_name, email = frappe.db.get_value("Sales Rep", field_order.sales_rep, ["full_name", "user"]) or (None, None)
	email = email or field_order.sales_rep
	values = {
		"field_order_no": field_order.name,
		"sales_rep": f"{full_name} <{email}>" if full_name and email else email or full_name or "",
	}
	return [
		{"namespace": METAFIELD_NAMESPACE, "key": key, "type": METAFIELD_TYPE, "value": value}
		for key, value in values.items()
		if value
	]


def _order_note(field_order) -> str:
	note = f"Field Order {field_order.name} \u00b7 Rep {field_order.sales_rep}"
	if field_order.location_id:
		note += f" \u00b7 Location {field_order.location_name or field_order.location_id}"
	return note


def _tax_line_item(field_order, currency: str) -> dict | None:
	"""
	The Field Order's tax as a custom, non-taxable line item.

	Draft orders take no explicit tax amount (Shopify only calculates tax from
	its own settings), so this is how the order total on the store matches
	the Field Order's grand total. The order-level discount then applies to
	net + tax, as it does on the Field Order.
	"""
	tax_amount = flt(field_order.tax_amount, 2)
	if tax_amount <= 0:
		return None
	return {
		"title": _("Tax ({0}%)").format(f"{flt(field_order.tax_percent):g}"),
		"quantity": 1,
		"originalUnitPriceWithCurrency": {"amount": str(tax_amount), "currencyCode": currency},
		"taxable": False,
		"requiresShipping": False,
	}


def _order_result(draft: dict) -> dict:
	"""DraftOrder node -> ``create_order`` result."""
	order = draft.get("order") or {}
	return {
		"draft_id": from_gid(draft.get("id")),
		"draft_name": draft.get("name"),
		"order_id": from_gid(order.get("id")),
		"order_name": order.get("name"),
		"checkout_url": draft.get("invoiceUrl"),
	}


def _normalize_order(order: dict | None) -> dict:
	"""Shopify order node -> neutral order dict; enums become lowercase words."""
	if not order:
		return {}

	fulfillment = (order.get("displayFulfillmentStatus") or "").lower()
	lines = []
	for node in (order.get("lineItems") or {}).get("nodes") or []:
		product = node.get("product") or {}
		discount = sum(
			flt(((a.get("allocatedAmountSet") or {}).get("shopMoney") or {}).get("amount"))
			for a in node.get("discountAllocations") or []
		)
		lines.append(
			{
				"line_id": from_gid(node.get("id")),
				"external_variant_id": from_gid((node.get("variant") or {}).get("id")),
				"qty": flt(node.get("quantity")),
				"current_qty": flt(node.get("currentQuantity")),
				"unit_price": flt(((node.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get("amount")),
				"discount": discount,
				"product_type": product.get("productType") or None,
				"tags": list(product.get("tags") or []),
			}
		)

	refunds = []
	for refund in order.get("refunds") or []:
		refunds.append(
			{
				"refund_id": from_gid(refund.get("id")),
				"lines": [
					{
						"line_id": from_gid((n.get("lineItem") or {}).get("id")),
						"qty": flt(n.get("quantity")),
						"amount": flt(((n.get("subtotalSet") or {}).get("shopMoney") or {}).get("amount")),
					}
					for n in (refund.get("refundLineItems") or {}).get("nodes") or []
				],
			}
		)

	return {
		"external_order_id": from_gid(order.get("id")),
		"currency": order.get("currencyCode"),
		"financial_status": (order.get("displayFinancialStatus") or "").lower(),
		"fulfillment_status": "partial" if fulfillment == "partially_fulfilled" else fulfillment,
		"cancelled": bool(order.get("cancelledAt")),
		"lines": lines,
		"refunds": refunds,
	}


def _normalize_variant(variant: dict) -> dict:
	"""Map one ``productVariants`` node to the normalized product dict (see ``connectors.base``)."""
	product = variant.get("product") or {}
	inventory_item = variant.get("inventoryItem") or {}
	collections = (product.get("collections") or {}).get("nodes") or []
	compare_at = variant.get("compareAtPrice")

	return {
		"external_product_id": from_gid(product.get("id")),
		"external_variant_id": from_gid(variant.get("id")),
		"external_inventory_id": from_gid(inventory_item.get("id")),
		"product_title": product.get("title") or "",
		"variant_title": "" if variant.get("title") == "Default Title" else (variant.get("title") or ""),
		"sku": variant.get("sku") or "",
		"barcode": variant.get("barcode") or "",
		"price": flt(variant.get("price")),
		"compare_at_price": flt(compare_at) if compare_at is not None else None,
		"collection": (collections[0].get("title") if collections else None) or product.get("productType") or "",
		"image_url": (variant.get("image") or {}).get("url") or (product.get("featuredImage") or {}).get("url") or "",
		# UNLISTED and any future status are treated as not sellable.
		"status": STATUS_MAP.get(product.get("status"), "Draft"),
		"track_inventory": bool(inventory_item.get("tracked")),
	}


def is_oauth(settings) -> bool:
	"""True when Store Connection uses the App Store (OAuth) install rather than client credentials."""
	return (settings.get("shopify_auth_mode") or app_config.CLIENT_CREDENTIALS_MODE) == app_config.OAUTH_MODE


def save_oauth_tokens(body: dict):
	"""
	Store a token response from ``/admin/oauth/access_token`` (code exchange or refresh) on
	Store Connection: access + refresh tokens encrypted, their expiries, granted scopes.
	Does not commit.

	:return: The access token's expiry (``None`` for a non-expiring token).
	"""
	now = now_datetime()
	token = body["access_token"]
	expiry = add_to_date(now, seconds=cint(body["expires_in"])) if body.get("expires_in") else None
	refresh = body.get("refresh_token")
	values = {"access_token": "*" * len(token), "token_expiry": expiry}

	set_encrypted_password("Store Connection", "Store Connection", token, "access_token")
	if refresh:
		set_encrypted_password("Store Connection", "Store Connection", refresh, "refresh_token")
		values["refresh_token"] = "*" * len(refresh)
		values["refresh_token_expiry"] = (
			add_to_date(now, seconds=cint(body["refresh_token_expires_in"]))
			if body.get("refresh_token_expires_in")
			else None
		)
	else:
		remove_encrypted_password("Store Connection", "Store Connection", "refresh_token")
		values.update(refresh_token=None, refresh_token_expiry=None)
	if body.get("scope"):
		values["shopify_scopes"] = body["scope"]
	frappe.db.set_single_value("Store Connection", values)
	return expiry


def clear_oauth_tokens() -> None:
	"""Forget the stored access and refresh tokens (uninstall, reauth, shop redact). Does not commit."""
	for field in ("access_token", "refresh_token"):
		remove_encrypted_password("Store Connection", "Store Connection", field)
	frappe.db.set_single_value(
		"Store Connection",
		{"access_token": None, "token_expiry": None, "refresh_token": None, "refresh_token_expiry": None},
	)


def _stored_datetime(field: str):
	"""A Store Connection Datetime straight from the table (``None`` if unset; see Gotchas on Singles)."""
	singles = frappe.qb.DocType("Singles")
	rows = (
		frappe.qb.from_(singles)
		.select(singles.value)
		.where((singles.doctype == "Store Connection") & (singles.field == field))
		.run()
	)
	return get_datetime(rows[0][0]) if rows and rows[0][0] else None


def normalize_shop_domain(store_url: str | None) -> str:
	"""
	Reduce whatever was pasted as Store URL to the shop's ``*.myshopify.com`` domain.

	- ``https://foo.myshopify.com/admin/`` -> ``foo.myshopify.com``
	- ``https://admin.shopify.com/store/foo/products`` -> ``foo.myshopify.com``

	Anything else (e.g. a custom storefront domain) is returned as its bare host;
	``is_myshopify_domain`` tells the caller whether it is usable.
	"""
	url = (store_url or "").strip().lower()
	for prefix in ("https://", "http://"):
		if url.startswith(prefix):
			url = url[len(prefix) :]

	host, _sep, path = url.partition("/")
	if host == "admin.shopify.com":
		parts = path.split("/")
		if len(parts) >= 2 and parts[0] == "store" and parts[1]:
			return f"{parts[1]}.myshopify.com"

	return host.strip()


def is_myshopify_domain(domain: str) -> bool:
	"""The Admin API is only served on the shop's ``*.myshopify.com`` domain."""
	return domain.endswith(".myshopify.com") and domain != ".myshopify.com"


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)


def _json_or_none(response: requests.Response) -> dict | None:
	try:
		body = response.json()
	except ValueError:
		return None
	return body if isinstance(body, dict) else None


def _error_text(body: dict | None, response: requests.Response) -> str:
	"""Best human-readable error from a Shopify error response."""
	if body:
		errors = body.get("errors") or body.get("error_description") or body.get("error")
		if isinstance(errors, str):
			return errors
		if isinstance(errors, list):
			return "; ".join(e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors)
		if isinstance(errors, dict):
			return frappe.as_json(errors)
	return (response.text or response.reason or "")[:500]


def _graphql_error(response: requests.Response, body: dict | None) -> ConnectorError | None:
	"""Return the ConnectorError a response represents, or ``None`` if it succeeded."""
	status = response.status_code

	if not response.ok:
		return ConnectorError(
			_("Shopify returned HTTP {0}: {1}").format(status, _error_text(body, response)),
			code=f"HTTP_{status}",
			http_status=status,
		)

	if body is None:
		return ConnectorError(_("Shopify returned an invalid response"), code="INVALID_RESPONSE", http_status=status)

	if body.get("errors"):
		errors = body["errors"]
		code = "GRAPHQL_ERROR"
		if isinstance(errors, list) and errors and isinstance(errors[0], dict):
			code = (errors[0].get("extensions") or {}).get("code") or code
		return ConnectorError(_error_text(body, response), code=code, http_status=status)

	# Mutations report validation problems as data.<mutation>.userErrors.
	for result in (body.get("data") or {}).values():
		user_errors = result.get("userErrors") if isinstance(result, dict) else None
		if user_errors:
			message = "; ".join(
				f"{'.'.join(e.get('field') or [])}: {e.get('message')}" if e.get("field") else e.get("message", "")
				for e in user_errors
			)
			return ConnectorError(message, code="USER_ERROR", http_status=status, details=user_errors)

	return None


def _respect_throttle(body: dict) -> None:
	"""Pause when the query-cost bucket is nearly empty, so the next call is not throttled."""
	throttle = ((body.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
	available = throttle.get("currentlyAvailable")
	if available is not None and available < THROTTLE_MIN_AVAILABLE:
		time.sleep(THROTTLE_SLEEP_SECONDS)
