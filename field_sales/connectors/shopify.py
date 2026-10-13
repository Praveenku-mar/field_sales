# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Shopify connector (Admin GraphQL API).

Auth uses the OAuth *client credentials* grant: the app's client id/secret
from Store Connection are exchanged for a short-lived access token, which is
cached encrypted on Store Connection and refreshed shortly before it expires
(or immediately on a 401).
"""

from __future__ import annotations

import re
import time
from datetime import timedelta

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime
from frappe.utils.password import set_encrypted_password

from field_sales.connectors.base import BaseConnector, ConnectorError

REQUEST_TIMEOUT = 30  # seconds
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
DEFAULT_TOKEN_TTL = 24 * 60 * 60  # used if Shopify omits expires_in
THROTTLE_MIN_AVAILABLE = 100  # query cost points
THROTTLE_SLEEP_SECONDS = 2
PAGE_SIZE = 50
STOCK_BATCH_SIZE = 50

STATUS_MAP = {"ACTIVE": "Active", "DRAFT": "Draft", "ARCHIVED": "Archived"}

PRODUCTS_QUERY = """
query($cursor: String) {
  productVariants(first: %d, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title sku barcode price compareAtPrice
      image { url }
      inventoryItem { id tracked }
      product { id title status productType featuredImage { url } collections(first: 1) { nodes { title } } }
    }
  }
}
""" % PAGE_SIZE

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

DRAFT_ORDER_FIELDS = "id name status order { id name }"

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

ORDER_SEARCH = """
query($q: String!) { orders(first: 1, query: $q) { nodes { id name } } }
"""

SHOP_CURRENCY_QUERY = "{ shop { currencyCode } }"

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
		if not settings.shopify_api_version:
			frappe.throw(_("Shopify API Version is required on Store Connection"))

		self.graphql_url = f"https://{self.shop_domain}/admin/api/{settings.shopify_api_version}/graphql.json"
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

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------

	def test_connection(self) -> dict:
		"""Query the shop's name; never raises for platform errors, returns ``ok=False`` instead."""
		try:
			data = self._graphql(
				"{ shop { name myshopifyDomain } }", operation="Test Connection"
			)
		except ConnectorError as e:
			return {"ok": False, "shop_name": "", "message": e.message}

		shop = data.get("shop") or {}
		return {
			"ok": True,
			"shop_name": shop.get("name") or "",
			"message": _("Connected to {0} ({1})").format(shop.get("name"), shop.get("myshopifyDomain")),
		}

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
		found by its tag.

		:return: ``{"draft_id", "order_id", "order_name"}`` (plain ids; order fields ``None`` in Draft mode).
		"""
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

	def get_order_url(self, order_id: str | None, draft_id: str | None = None) -> str | None:
		if order_id:
			return f"https://{self.shop_domain}/admin/orders/{from_gid(order_id)}"
		if draft_id:
			return f"https://{self.shop_domain}/admin/draft_orders/{from_gid(draft_id)}"
		return None

	def _create_confirmed_order(self, field_order, retailer) -> dict:
		"""``orderCreate`` with explicit tax, discount and payment pending; returns ``{"id", "name"}``."""
		currency = self._get_currency(field_order)
		address = _customer_address(retailer, to_e164_india(retailer.phone))

		order_input = {
			"customer": {"toAssociate": {"id": to_gid("Customer", retailer.external_customer_id)}},
			"lineItems": [_order_line_item(row, currency) for row in field_order.items],
			"taxesIncluded": False,
			"financialStatus": "PENDING",
			"note": _order_note(field_order),
			"tags": _order_tags(field_order),
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
		data = self._graphql(
			ORDER_SEARCH,
			{"q": f'tag:"{_field_order_tag(field_order)}"'},
			operation="Confirm Order",
			ref=field_order,
		)
		nodes = (data.get("orders") or {}).get("nodes") or []
		return nodes[0] if nodes else None

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
		address = _customer_address(retailer, to_e164_india(retailer.phone))

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

	def _get_currency(self, field_order) -> str:
		"""The shop's currency; order prices are sent in it."""
		if self._currency is None:
			data = self._graphql(SHOP_CURRENCY_QUERY, operation="Create Order", ref=field_order)
			self._currency = (data.get("shop") or {}).get("currencyCode")
		return self._currency

	# ------------------------------------------------------------------
	# Auth
	# ------------------------------------------------------------------

	def _get_token(self, force_refresh: bool = False) -> str:
		"""
		Return a valid access token, requesting a new one if needed.

		The stored token is reused while it has more than ``TOKEN_REFRESH_MARGIN``
		left. ``force_refresh`` skips that check (used after a 401).
		"""
		if not force_refresh:
			if self._token is None:
				self._token = self.settings.get_password("access_token", raise_exception=False)
				self._token_expiry = self.settings.token_expiry

			if (
				self._token
				and self._token_expiry
				and get_datetime(self._token_expiry) > now_datetime() + TOKEN_REFRESH_MARGIN
			):
				return self._token

		return self._request_new_token()

	def _request_new_token(self) -> str:
		"""Run the client-credentials grant, store the token encrypted, and commit."""
		client_id = self.settings.shopify_client_id
		client_secret = self.settings.get_password("shopify_client_secret", raise_exception=False)
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
			if error and error.code == "ACCESS_DENIED" and attempt == 1 and not self._token_is_fresh:
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


def to_e164_india(phone: str | None) -> str | None:
	"""``"98765 43210"`` / ``"+91-9876543210"`` -> ``"+919876543210"``; ``None`` if under 10 digits."""
	digits = re.sub(r"\D", "", phone or "")
	return f"+91{digits[-10:]}" if len(digits) >= 10 else None


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
	phone = to_e164_india(retailer.phone)
	if phone:
		customer_input["phone"] = phone
	return customer_input


def _customer_address(retailer, phone: str | None) -> dict:
	"""Retailer address -> MailingAddressInput. ``province`` is deprecated but still accepted."""
	address = {
		"address1": retailer.address_line1 or "",
		"address2": retailer.address_line2 or "",
		"city": retailer.city or "",
		"province": retailer.state or "",
		"zip": retailer.pincode or "",
		"countryCode": "IN",
		"company": retailer.retailer_name,
	}
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


def _field_order_tag(field_order) -> str:
	"""Tag that identifies the Field Order's order on Shopify, used to find it again on retry."""
	return f"field-order-{field_order.name}"


def _order_tags(field_order) -> list[str]:
	return [t for t in ("field-sales", field_order.sales_rep, _field_order_tag(field_order)) if t]


def _order_note(field_order) -> str:
	return f"Field Order {field_order.name} \u00b7 Rep {field_order.sales_rep}"


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
		"order_id": from_gid(order.get("id")),
		"order_name": order.get("name"),
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
