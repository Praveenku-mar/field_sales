# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Odoo connector.

Talks to the store through Odoo's JSON-RPC endpoint (``/jsonrpc``) with a
user's API key: every call is a stateless ``execute_kw`` carrying the key, so
no session has to be kept alive.

Mappings worth knowing:

- Locations are ``stock.warehouse`` records; stock is read in the warehouse's
  ``lot_stock_id`` location, so ``qty_available`` is warehouse-scoped. A store
  without the Inventory app (an eCommerce-only Odoo) reports the single
  pseudo-location ``"odoo"`` instead, backed by the company-wide ``qty_available``.
- ``order_mode`` maps to the sale order lifecycle: ``Draft`` leaves the order
  in Odoo's draft state, ``Confirmed`` runs ``action_confirm`` on it. A draft
  and its confirmed order are the *same* record in Odoo, so ``draft_id`` and
  ``order_id`` are one id and ``get_completed_drafts`` reports the drafts whose
  state has since left ``draft``/``sent``.
- Order totals are sent as untaxed unit prices; the per-product taxes Odoo
  would apply are cleared on the lines, and the Field Order's tax percent is
  recorded in the order's note instead (a flat-tax line needs a tax record
  that cannot be assumed to exist on every database). A flat discount is
  applied per line as a percent.
- Odoo has no native signed webhooks, so ``verify_webhook`` always refuses:
  nothing unverified is ever accepted. Catalog and stock updates arrive through
  the scheduled syncs; a merchant wanting pushes can front
  ``field_sales.api.webhooks.receive`` with a signing proxy later.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import frappe
import requests
from frappe import _

from field_sales.connectors.base import (
	ORDER_CANCELLED_TOPIC,
	ORDER_FULFILLED_TOPIC,
	ORDER_PAID_TOPIC,
	PRODUCT_DELETE_TOPIC,
	PRODUCT_UPDATE_TOPIC,
	BaseConnector,
	ConnectorError,
)

if TYPE_CHECKING:
	from frappe.model.document import Document

TIMEOUT = 30
PAGE_SIZE = 100

#: The single stock pool reported when the store has no Inventory app.
STOCK_LOCATION_ID = "odoo"
STOCK_LOCATION_NAME = "Odoo Stock"

#: Sale order states that mean "still a draft" on the store.
DRAFT_STATES = ("draft", "sent")
#: Sale order states that mean the draft has been completed into an order.
DONE_STATES = ("sale", "lock", "done")


class OdooConnector(BaseConnector):
	platform = "Odoo"

	def __init__(self, settings: Document):
		super().__init__(settings)
		self.base_url = self.normalize_store_url(settings.store_url).rstrip("/")
		self.rpc_url = f"{self.base_url}/jsonrpc"
		self.database = (settings.odoo_database or "").strip()
		self.username = (settings.odoo_username or "").strip()
		# The loaded single holds the masked dummy for a saved key; get_password
		# returns the stored one.
		self.api_key = settings.get_password("odoo_api_key", raise_exception=False) or ""
		if not self.database or not self.username or not self.api_key:
			frappe.throw(_("Odoo Database, Username and API Key are required on Store Connection"))
		self._uid: int | None = None
		#: warehouse id -> the stock.location id its qty_available is scoped to
		self._warehouse_locations: dict[str, int | None] = {}
		#: True once the store proves it has no Inventory app (no stock.warehouse)
		self._no_stock_module = False
		#: None = not probed yet, True/False = product.product has is_published
		#: (it comes from website_sale, which a plain Odoo sale install lacks)
		self._has_is_published: bool | None = None
		#: None = not probed yet; False on Odoo 18+, which dropped the field
		self._sale_payment_state: bool | None = None
		#: payment.transaction's link to the order: name varies by version
		#: ("" once probed and absent); None = not probed yet
		self._transaction_field: str | None = None

	# ------------------------------------------------------------------
	# RPC layer
	# ------------------------------------------------------------------

	def _rpc(self, service: str, method: str, args: list, operation: str, ref: Document | None = None) -> object:
		"""One JSON-RPC call; ``args`` never contains anything but ids and values."""
		body = {
			"jsonrpc": "2.0",
			"method": "call",
			"id": int(time.time()),
			"params": {"service": service, "method": method, "args": args},
		}
		start = time.monotonic()
		try:
			response = requests.post(self.rpc_url, json=body, timeout=TIMEOUT)
		except requests.exceptions.RequestException as e:
			self._write_log(
				operation,
				ref,
				status="Failed",
				duration_ms=_elapsed_ms(start),
				error_code="NETWORK_ERROR",
				error_message=str(e)[:500],
			)
			raise ConnectorError(
				_("Could not reach the Odoo store at {0}").format(self.base_url), code="NETWORK_ERROR"
			) from e

		if response.status_code != 200:
			self._write_log(
				operation,
				ref,
				status="Failed",
				duration_ms=_elapsed_ms(start),
				http_status=response.status_code,
				error_code="HTTP_ERROR",
				error_message=_truncate(response.text),
			)
			raise ConnectorError(
				_("Odoo {0} {1} failed (HTTP {2})").format(service, method, response.status_code),
				code="HTTP_ERROR",
				http_status=response.status_code,
			)

		try:
			payload = response.json()
		except ValueError as e:
			self._write_log(
				operation,
				ref,
				status="Failed",
				duration_ms=_elapsed_ms(start),
				http_status=200,
				error_code="BAD_RESPONSE",
				error_message=_truncate(response.text),
			)
			raise ConnectorError(_("Odoo returned a response that is not JSON"), code="BAD_RESPONSE") from e

		if payload.get("error"):
			error = self._odoo_error(operation, ref, start, payload["error"])
			raise error

		self._write_log(
			operation,
			ref,
			status="Success",
			duration_ms=_elapsed_ms(start),
			http_status=200,
			request_payload=_safe_summary(service, method, args),
			response_payload=_truncate(frappe.as_json(payload.get("result"))),
		)
		return payload.get("result")

	def _odoo_error(self, operation: str, ref: Document | None, start: float, error: dict) -> ConnectorError:
		"""Turn Odoo's JSON-RPC error envelope into a logged ``ConnectorError``."""
		data = error.get("data") or {}
		name = (data.get("name") or "").rsplit(".", 1)[-1]
		message = data.get("message") or error.get("message") or _("Odoo error")
		lowered = str(message).lower()

		if name in ("AccessDenied", "AuthenticationError") or "session expired" in lowered:
			code = "AUTH_FAILED"
		elif name == "MissingError" or "does not exist" in lowered or "doesn't exist" in lowered:
			code = "NOT_FOUND"
		elif "invalid field" in lowered or "unknown field" in lowered:
			code = "BAD_FIELD"
		else:
			code = "ODOO_ERROR"

		self._write_log(
			operation,
			ref,
			status="Failed",
			duration_ms=_elapsed_ms(start),
			http_status=200,
			error_code=code,
			error_message=(f"{name}: " if name else "") + _truncate(str(message)),
		)
		if code == "AUTH_FAILED":
			message = _("Odoo rejected the Database, Username or API Key. Check them on Store Connection.")
		return ConnectorError(str(message), code=code, details=[{"name": name, "message": str(message)[:500]}])

	def _authenticate(self, operation: str = "Other") -> int:
		if self._uid:
			return self._uid
		uid = self._rpc(
			"common", "authenticate", [self.database, self.username, self.api_key, {}], operation
		)
		if not isinstance(uid, int) or not uid:
			# Odoo answers a plain ``false`` for wrong credentials, not an error.
			self._write_log(operation, status="Failed", error_code="AUTH_FAILED",
				error_message=_("Odoo rejected the Database, Username or API Key"))
			raise ConnectorError(
				_("Odoo rejected the Database, Username or API Key. Check them on Store Connection."),
				code="AUTH_FAILED",
			)
		self._uid = uid
		return uid

	def _execute_kw(
		self,
		model: str,
		method: str,
		args: list,
		operation: str = "Other",
		ref: Document | None = None,
		kwargs: dict | None = None,
	) -> object:
		uid = self._authenticate(operation)
		return self._rpc(
			"object", "execute_kw", [self.database, uid, self.api_key, model, method, args, kwargs or {}],
			operation, ref,
		)

	def _search_read(
		self,
		model: str,
		domain: list,
		fields: list,
		operation: str,
		ref: Document | None = None,
		context: dict | None = None,
		limit: int | None = None,
		offset: int | None = None,
	) -> list[dict]:
		kwargs: dict = {"fields": fields}
		if context:
			kwargs["context"] = context
		if limit:
			kwargs["limit"] = limit
		if offset:
			kwargs["offset"] = offset
		return self._execute_kw(model, "search_read", [domain], operation, ref, kwargs) or []

	def _read(self, model: str, ids: list, fields: list, operation: str, ref: Document | None = None) -> list[dict]:
		return self._execute_kw(model, "read", [ids, fields], operation, ref) or []

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------

	@staticmethod
	def normalize_store_url(url: str | None) -> str:
		url = (url or "").strip()
		if not url:
			frappe.throw(_("Store URL is required on Store Connection"))
		if not url.startswith(("http://", "https://")):
			url = f"https://{url}"
		return url.rstrip("/")

	def test_connection(self) -> dict:
		try:
			uid = self._authenticate(operation="Test Connection")
			user = self._read("res.users", [uid], ["name", "tz", "company_id"], "Test Connection")[0]
			company_id = (user.get("company_id") or [False])[0]
			company = self._read("res.company", [company_id], ["name", "currency_id", "partner_id"],
				"Test Connection")[0]
			currency = ((company.get("currency_id") or [False, ""])[1] or "").strip()
			timezone = (user.get("tz") or "").strip()
			country = self._company_country_code(company.get("partner_id"), "Test Connection")
			name = (company.get("name") or "").strip() or self.base_url.split("//")[-1]
		except (ConnectorError, IndexError) as e:
			message = e.message if isinstance(e, ConnectorError) else _("Odoo returned an incomplete profile")
			return {"ok": False, "shop_name": "", "shop_domain": "", "scopes": [], "currency": "",
				"timezone": "", "country": "", "message": message}
		return {
			"ok": True,
			"shop_name": name,
			"shop_domain": self.base_url.split("//")[-1],
			"scopes": ["rpc"],
			"currency": currency,
			"timezone": timezone,
			"country": country,
			"message": _("Connected to {0} (Odoo)").format(name),
		}

	def get_shop_settings(self) -> dict:
		uid = self._authenticate()
		user = self._read("res.users", [uid], ["tz", "company_id"], "Other")[0]
		company = self._read("res.company", [(user.get("company_id") or [False])[0]],
			["currency_id", "partner_id"], "Other")[0]
		return {
			"currency": ((company.get("currency_id") or [False, ""])[1] or "").strip(),
			"timezone": (user.get("tz") or "").strip(),
			"country": self._company_country_code(company.get("partner_id"), "Other"),
		}

	def _company_country_code(self, partner_ref, operation: str) -> str:
		"""The ISO code of the company's address, blank when it has none set."""
		if not partner_ref:
			return ""
		partner = self._read("res.partner", [partner_ref[0]], ["country_id"], operation)[0]
		country = partner.get("country_id")
		return (country[1] if country else "")[:2].upper()

	def _country_id(self, country: str | None):
		"""The res.country id behind an ISO code or full name, ``False`` when unknown."""
		code = (country or "").strip()
		if not code:
			return False
		found = self._search_read("res.country", [("code", "=", code)], ["id"], "Other", limit=1)
		if not found:
			found = self._search_read("res.country", [("name", "=", code)], ["id"], "Other", limit=1)
		return found[0]["id"] if found else False

	def _state_id(self, state: str | None, country_id):
		"""The res.country.state id behind a state name, within the country."""
		name = (state or "").strip()
		if not name or not country_id:
			return False
		found = self._search_read(
			"res.country.state", [("name", "=", name), ("country_id", "=", country_id)], ["id"],
			"Other", limit=1,
		)
		return found[0]["id"] if found else False

	def get_locations(self) -> list[dict]:
		"""Warehouses are the pickable stock locations; each has one stock location."""
		try:
			warehouses = self._search_read("stock.warehouse", [], ["id", "name", "lot_stock_id"], "Other")
		except ConnectorError as e:
			if e.code == "NOT_FOUND" and "stock.warehouse" in e.message:
				# No Inventory app on the store (an eCommerce-only Odoo): one
				# company-wide stock pool, exactly like the WooCommerce connector.
				self._warehouse_locations = {}
				self._no_stock_module = True
				return [{"id": STOCK_LOCATION_ID, "name": _(STOCK_LOCATION_NAME)}]
			raise
		self._no_stock_module = False
		self._warehouse_locations = {
			str(w["id"]): (w["lot_stock_id"][0] if w.get("lot_stock_id") else None) for w in warehouses
		}
		return [{"id": str(w["id"]), "name": w["name"]} for w in warehouses]

	def _stock_context(self, location_id: str) -> dict | None:
		"""Read ``qty_available`` inside one warehouse: scope it to its stock location."""
		if not location_id or getattr(self, "_no_stock_module", False):
			return None
		if location_id == STOCK_LOCATION_ID:
			return None
		if not self._warehouse_locations:
			self.get_locations()
		stock_location = self._warehouse_locations.get(str(location_id))
		return {"location": stock_location} if stock_location else None

	def get_products(self, cursor: str | None = None) -> dict:
		offset = int(cursor or 0)
		rows = self._search_products(
			[("sale_ok", "=", True)], "Product Sync", limit=PAGE_SIZE, offset=offset
		)
		items = [item for row in rows if (item := self._normalize_product(row))]
		next_cursor = str(offset + PAGE_SIZE) if len(rows) == PAGE_SIZE else None
		return {"items": items, "next_cursor": next_cursor}

	def get_product(self, external_product_id: str) -> list[dict]:
		try:
			rows = self._search_products(
				[("product_tmpl_id", "=", int(external_product_id))], "Product Sync"
			)
		except ConnectorError as e:
			if e.code == "NOT_FOUND":
				return []
			raise
		return [item for row in rows if (item := self._normalize_product(row))]

	def get_stock(self, inventory_ids: list, location_id: str) -> dict:
		result = {str(i): 0 for i in inventory_ids}
		ids = sorted({int(i) for i in inventory_ids if str(i).isdigit()})
		if not ids:
			return result
		rows = self._search_read(
			"product.product", [("id", "in", ids)], ["id", "qty_available"], "Stock Check",
			context=self._stock_context(location_id),
		)
		for row in rows:
			result[str(row["id"])] = frappe.utils.flt(row.get("qty_available"))
		return result

	def create_customer(self, retailer: Document) -> dict:
		email = (retailer.email or "").strip().lower() or _placeholder_email(retailer)
		country_id = self._country_id(retailer.country)
		values = _partner_values(retailer, email, country_id, self._state_id(retailer.state, country_id))

		if retailer.external_customer_id:
			self._execute_kw(
				"res.partner", "write", [[int(retailer.external_customer_id)], values],
				"Update Customer", ref=retailer,
			)
			return {"id": str(retailer.external_customer_id), "warning": None}

		# Odoo allows duplicate emails; reuse the partner already behind one so
		# orders land on a single customer record.
		found = self._search_read("res.partner", [("email", "=", email)], ["id"],
			"Create Customer", ref=retailer, limit=1)
		if found:
			return {"id": str(found[0]["id"]), "warning": None}

		partner_id = self._execute_kw(
			"res.partner", "create", [values], "Create Customer", ref=retailer
		)
		return {"id": str(partner_id), "warning": _email_warning(retailer)}

	def create_order(self, field_order: Document) -> dict:
		# Retry-safe: an order already on the store is reused, never duplicated.
		if field_order.external_order_id and str(field_order.external_order_id).isdigit():
			found = self._read(
				"sale.order", [int(field_order.external_order_id)], ["name"], "Create Order", ref=field_order
			)
			if found:
				return {"draft_id": None, "draft_name": None,
					"order_id": str(field_order.external_order_id), "order_name": found[0]["name"],
					"checkout_url": None}

		confirmed = self.settings.order_mode == "Confirmed"
		operation = "Confirm Order" if confirmed else "Create Order"
		order_id = self._find_order_by_ref(field_order, operation)
		if order_id is None:
			values = _order_values(field_order)
			order_id = self._execute_kw("sale.order", "create", [values], operation, ref=field_order)

		if confirmed:
			try:
				self._execute_kw("sale.order", "action_confirm", [[order_id]], operation, ref=field_order)
			except ConnectorError as e:
				# The draft exists even though confirming it failed; keep its id
				# so the retry resumes from it instead of creating another.
				raise ConnectorError(e.message, code=e.code, details=e.details,
					partial={"draft_id": str(order_id)}) from e

		name = self._read("sale.order", [order_id], ["name"], operation, ref=field_order)[0]["name"]
		if confirmed:
			return {"draft_id": None, "draft_name": None,
				"order_id": str(order_id), "order_name": name, "checkout_url": None}
		return {"draft_id": str(order_id), "draft_name": name,
			"order_id": None, "order_name": None, "checkout_url": None}

	def _find_order_by_ref(self, field_order: Document, operation: str) -> int | None:
		"""The draft a failed earlier attempt left behind, matched by our order no."""
		if not field_order.name:
			return None
		found = self._search_read(
			"sale.order", [("client_order_ref", "=", field_order.name)], ["id"],
			operation, ref=field_order, limit=1,
		)
		return found[0]["id"] if found else None

	def _has_payment_state(self) -> bool:
		"""Odoo 18+ removed sale.order's payment_state; probe once per connector."""
		if self._sale_payment_state is None:
			found = self._execute_kw(
				"sale.order", "fields_get", [["payment_state"]], "Other", kwargs={"attributes": ["type"]}
			)
			self._sale_payment_state = isinstance(found, dict) and "payment_state" in found
		return self._sale_payment_state

	def _order_has_paid_transaction(self, order_id: int) -> bool:
		"""The fallback paid signal: a done/authorized payment transaction on the order."""
		field = self._transaction_order_field()
		if not field:
			return False
		try:
			rows = self._search_read(
				"payment.transaction", [(field, "=", order_id)], ["state"], "Other"
			)
		except ConnectorError as e:
			if e.code == "NOT_FOUND":  # no payment app on the store at all
				return False
			raise
		return any(r.get("state") in ("done", "authorized") for r in rows)

	def _transaction_order_field(self) -> str:
		"""How payment.transaction points at a sale order: probed once, ``""`` when nothing."""
		if self._transaction_field is not None:
			return self._transaction_field
		try:
			found = self._execute_kw(
				"payment.transaction", "fields_get", [["sale_order_id", "sale_id"]], "Other",
				kwargs={"attributes": ["type"]},
			)
		except ConnectorError as e:
			if e.code == "NOT_FOUND":  # no payment app on the store at all
				self._transaction_field = ""
				return ""
			raise
		for candidate in ("sale_order_id", "sale_id"):
			if isinstance(found, dict) and candidate in found:
				self._transaction_field = candidate
				return candidate
		self._transaction_field = ""
		return ""

	def mark_order_paid(self, field_order: Document) -> dict:
		if not str(field_order.external_order_id or "").isdigit():
			raise ConnectorError(
				_("Order {0} no longer exists on the Odoo store").format(field_order.external_order_id),
				code="NOT_FOUND",
			)
		order_id = int(field_order.external_order_id)
		if not self._has_payment_state():
			# Nothing on the order to write; only a real gateway payment can say paid.
			if self._order_has_paid_transaction(order_id):
				return {"financial_status": "paid", "already_paid": True}
			raise ConnectorError(
				_("This Odoo has no sale-order payment state to mark paid; payments taken on"
				" Field Sales are not mirrored onto the store order"),
				code="CANNOT_MARK_PAID",
			)
		rows = self._read("sale.order", [order_id], ["payment_state"], "Mark Order Paid", ref=field_order)
		if not rows:
			raise ConnectorError(
				_("Order {0} no longer exists on the Odoo store").format(field_order.external_order_id),
				code="NOT_FOUND",
			)
		if rows[0].get("payment_state") == "paid":
			return {"financial_status": "paid", "already_paid": True}
		try:
			self._execute_kw(
				"sale.order", "write", [[order_id], {"payment_state": "paid"}],
				"Mark Order Paid", ref=field_order,
			)
		except ConnectorError as e:
			raise ConnectorError(
				_("Odoo would not mark order {0} paid: {1}").format(field_order.external_order_id, e.message),
				code="CANNOT_MARK_PAID", details=e.details,
			) from e
		return {"financial_status": "paid", "already_paid": False}

	def get_draft_names(self, draft_ids: list) -> dict:
		rows = self._read_orders(draft_ids, ["id", "name", "state"], "Other")
		return {str(r["id"]): r["name"] for r in rows if r["state"] in DRAFT_STATES}

	def get_completed_drafts(self, draft_ids: list) -> dict:
		"""Odoo drafts become orders in place: the draft id *is* the order id."""
		rows = self._read_orders(draft_ids, ["id", "state"], "Other")
		return {str(r["id"]): str(r["id"]) for r in rows if r["state"] in DONE_STATES}

	def _read_orders(self, draft_ids: list, fields: list, operation: str) -> list[dict]:
		ids = [int(i) for i in draft_ids if str(i).isdigit()]
		if not ids:
			return []
		return self._read("sale.order", ids, fields, operation)

	def ensure_order_metafields(self) -> bool:
		# The Field Order No rides on sale.order's built-in client_order_ref.
		return True

	def set_order_metafields(self, order_id: str, field_order: Document) -> None:
		self._execute_kw(
			"sale.order", "write", [[int(order_id)], {
				"client_order_ref": field_order.name,
				"note": _order_note(field_order),
			}],
			"Other", ref=field_order,
		)

	def get_order_url(self, order_id: str | None, draft_id: str | None = None) -> str | None:
		external_id = order_id or draft_id
		if not external_id:
			return None
		return f"{self.base_url}/odoo/sale.order/{external_id}"

	def get_order(self, external_id: str) -> dict:
		if not str(external_id or "").isdigit():
			return {}
		fields = ["name", "currency_id", "state", "order_line", "invoice_status"]
		if self._has_payment_state():
			fields.append("payment_state")
		try:
			orders = self._read("sale.order", [int(external_id)], fields, "Commission")
		except ConnectorError as e:
			if e.code == "NOT_FOUND":
				return {}
			raise
		if not orders:
			return {}
		lines = self._read(
			"sale.order.line", [lid for lid in orders[0]["order_line"]],
			["product_id", "product_uom_qty", "qty_delivered", "price_unit", "price_subtotal"], "Commission",
		)
		financial = None
		if "payment_state" not in orders[0]:
			financial = "paid" if self._order_has_paid_transaction(int(external_id)) else "pending"
		return _normalize_order(orders[0], lines, financial)

	# ------------------------------------------------------------------
	# Webhooks (unsupported; see the module docstring)
	# ------------------------------------------------------------------

	def verify_webhook(self, headers, raw_body: bytes) -> bool:
		# Odoo cannot sign webhooks natively, so nothing is accepted: unsigned
		# events are exactly what this check exists to refuse.
		return False

	def parse_webhook(self, headers, raw_body: bytes) -> dict:
		payload = json.loads(raw_body.decode() or "{}")
		topic = payload.get("topic") or payload.get("event") or "other"
		return {
			"topic": _map_topic(topic, payload),
			"platform_topic": str(topic),
			"event_id": str(payload.get("event_id") or "") or None,
			"updated_at": payload.get("write_date"),
			"payload": payload,
		}

	def handle_stock_event(self, payload: dict) -> dict | None:
		if "qty_available" not in payload or not payload.get("id"):
			return None
		return {
			"external_inventory_id": str(payload["id"]),
			"location_id": self.settings.default_location_id or "",
			"available": frappe.utils.flt(payload.get("qty_available")),
		}

	def handle_product_event(self, payload: dict) -> list[dict] | None:
		if not payload.get("id"):
			return None
		return [{
			"external_variant_id": str(payload["id"]),
			"price": frappe.utils.flt(payload.get("lst_price")) or None,
			"compare_at_price": None,
		}]

	def order_id_from_event(self, payload: dict) -> str | None:
		return str(payload["id"]) if payload.get("id") else None

	# ------------------------------------------------------------------
	# Internals
	# ------------------------------------------------------------------

	def _search_products(self, domain: list, operation: str, limit: int | None = None,
		offset: int | None = None) -> list[dict]:
		"""search_read on product.product, tolerating a missing website field."""
		fields = ["id", "name", "product_tmpl_id", "default_code", "barcode", "lst_price",
			"categ_id", "type", "is_storable", "qty_available"]
		if self._has_is_published is not False:
			fields.append("is_published")
		try:
			rows = self._search_read("product.product", domain, fields, operation,
				limit=limit, offset=offset)
			if "is_published" in fields:
				# Only the probing call can prove the field exists; the retry
				# below must not flip the verdict back.
				self._has_is_published = True
			return rows
		except ConnectorError as e:
			if self._has_is_published is None and e.code == "BAD_FIELD":
				self._has_is_published = False
				return self._search_products(domain, operation, limit=limit, offset=offset)
			raise

	def _normalize_product(self, row: dict) -> dict | None:
		template = row.get("product_tmpl_id") or [row["id"], row.get("name") or ""]
		published = row.get("is_published", True)
		sku = row.get("default_code") or None
		return {
			"external_product_id": str(template[0]),
			"external_variant_id": str(row["id"]),
			"external_inventory_id": str(row["id"]),
			"product_title": _strip_code_prefix(template[1] or row.get("name") or "", sku),
			"variant_title": _variant_title(row.get("name") or "", template[1] or ""),
			"sku": sku,
			"barcode": row.get("barcode") or None,
			"price": frappe.utils.flt(row.get("lst_price")),
			"compare_at_price": None,
			"collection": (row.get("categ_id") or [None, None])[1],
			"image_url": f"{self.base_url}/web/image/product.product/{row['id']}/image_1920",
			"status": "Active" if published else "Draft",
			# Odoo 17+ tracks stock on a flag; before that, type "product" meant storable.
			"track_inventory": bool(row.get("is_storable", row.get("type") == "product")),
		}


# ----------------------------------------------------------------------
# Payload helpers (module level so they read as data, not methods)
# ----------------------------------------------------------------------


def _partner_values(retailer: Document, email: str, country_id=False, state_id=False) -> dict:
	"""A res.partner create/write body from the Retailer's fields."""
	return {
		"name": (retailer.retailer_name or retailer.name or "").strip(),
		"email": email,
		"phone": retailer.phone or False,
		"street": retailer.address_line1 or False,
		"street2": retailer.address_line2 or False,
		"city": retailer.city or False,
		"zip": retailer.pincode or False,
		"country_id": country_id,
		"state_id": state_id,
		"customer_rank": 1,
	}


def _placeholder_email(retailer: Document) -> str:
	return f"{frappe.scrub(retailer.name)}@customers.fieldsales.local"


def _email_warning(retailer: Document) -> str | None:
	if not (retailer.email or "").strip():
		return _("No email on the customer; created with a placeholder email on the store")
	return None


def _order_values(field_order: Document) -> dict:
	customer_id = frappe.db.get_value("Retailer", field_order.retailer, "external_customer_id")
	if not customer_id or not str(customer_id).isdigit():
		raise ConnectorError(
			_("Customer {0} does not exist on the Odoo store yet").format(field_order.retailer),
			code="CUSTOMER_MISSING",
		)
	values = {
		"partner_id": int(customer_id),
		"client_order_ref": field_order.name,
		"note": _order_note(field_order),
		"order_line": _order_lines(field_order),
	}
	warehouse = (frappe.db.get_single_value("Store Connection", "default_location_id") or "").strip()
	if warehouse.isdigit():
		values["warehouse_id"] = int(warehouse)
	return values


def _order_lines(field_order: Document) -> list:
	"""Field Order Items as sale.order.line commands.

	Unit prices are the Field Order's, the lines' taxes are cleared so Odoo
	does not recalculate them, and a flat discount is spread as one percent.
	"""
	rows = [row for row in field_order.items if row.product]
	gross = sum(frappe.utils.flt(row.qty) * frappe.utils.flt(row.rate) for row in rows)
	discount = frappe.utils.flt(field_order.discount_amount)
	percent = round(discount / gross * 100, 2) if gross else 0

	lines = []
	for row in rows:
		variant_id = row.external_variant_id or frappe.db.get_value(
			"Platform Product", row.product, "external_variant_id"
		)
		if not variant_id or not str(variant_id).isdigit():
			continue
		lines.append((0, 0, {
			"product_id": int(variant_id),
			"product_uom_qty": frappe.utils.flt(row.qty),
			"price_unit": frappe.utils.flt(row.rate),
			"discount": percent or 0,
			"tax_ids": [(5, 0, 0)],
		}))
	if not lines:
		frappe.throw(_("The order has no items that exist on the Odoo store"))
	return lines


def _order_note(field_order: Document) -> str:
	rep = frappe.db.get_value("Sales Rep", field_order.sales_rep, "full_name") or field_order.sales_rep or ""
	notes = [f"Field Order No: {field_order.name}", f"Sales Rep: {rep}"]
	if frappe.utils.flt(field_order.tax_percent):
		notes.append(_("Tax {0}% is recorded on Field Sales; Odoo totals are untaxed.").format(
			f"{frappe.utils.flt(field_order.tax_percent):g}"))
	return "\n".join(notes)


def _strip_code_prefix(title: str, sku: str | None) -> str:
	"""Odoo shows product names as ``[SKU] Name`` when a code is set; the code
	is already its own column here, so drop the bracketed duplicate."""
	if sku and title.startswith(f"[{sku}] "):
		return title[len(sku) + 3:]
	return title


def _variant_title(display_name: str, template_name: str) -> str | None:
	"""Odoo's variant display name is ``Template (Attr)``; return the ``Attr`` part."""
	if not display_name or not template_name or display_name == template_name:
		return None
	prefix = f"{template_name} ("
	if display_name.startswith(prefix) and display_name.endswith(")"):
		return display_name[len(prefix):-1] or None
	return display_name


def _normalize_order(order: dict, lines: list[dict], financial: str | None = None) -> dict:
	state = order.get("state", "")
	payment_state = order.get("payment_state", "")
	if financial is None:
		financial = {
			"paid": "paid", "in_payment": "paid", "reversed": "refunded",
		}.get(payment_state, "pending")

	delivered = any(frappe.utils.flt(line.get("qty_delivered")) for line in lines)
	if state == "done":
		fulfillment = "fulfilled"
	elif state == "sale" and delivered:
		fulfillment = "partial"
	else:
		fulfillment = "unfulfilled"

	normalized_lines = []
	for line in lines:
		qty = frappe.utils.flt(line.get("product_uom_qty"))
		unit_price = frappe.utils.flt(line.get("price_unit"))
		normalized_lines.append({
			"line_id": str(line.get("id")),
			"external_variant_id": str((line.get("product_id") or [None])[0] or "") or None,
			"qty": qty,
			"current_qty": qty,
			"unit_price": unit_price,
			"discount": round(unit_price * qty, 2) - frappe.utils.flt(line.get("price_subtotal")),
			"product_type": None,
			"tags": [],
		})

	return {
		"external_order_id": str(order.get("id")),
		"currency": ((order.get("currency_id") or [None, ""])[1] or "").strip(),
		"financial_status": financial,
		"fulfillment_status": fulfillment,
		"cancelled": state == "cancel",
		"lines": normalized_lines,
		"refunds": [],
	}


def _map_topic(topic: str, payload: dict) -> str:
	"""A signing proxy can name Odoo events; map the obvious ones."""
	mapping = {
		"product.update": PRODUCT_UPDATE_TOPIC,
		"product.delete": PRODUCT_DELETE_TOPIC,
		"order.paid": ORDER_PAID_TOPIC,
		"order.done": ORDER_FULFILLED_TOPIC,
		"order.cancel": ORDER_CANCELLED_TOPIC,
	}
	return mapping.get(str(topic), str(topic) or "other")


def _safe_summary(service: str, method: str, args: list) -> str:
	"""A log line that cannot leak the API key carried in ``args``."""
	summary = {"service": service, "method": method}
	for arg in args:
		if isinstance(arg, str) and arg in ("product.product", "sale.order", "res.partner",
			"res.users", "res.company", "stock.warehouse", "sale.order.line",
			"res.country", "res.country.state"):
			summary["model"] = arg
	summary["args"] = len(args)
	return _truncate(frappe.as_json(summary))


def _truncate(text: str) -> str:
	return (text or "")[:5000]


def _elapsed_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)
