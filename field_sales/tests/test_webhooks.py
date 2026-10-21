# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import base64
import hashlib
import hmac
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime
from frappe.utils.password import set_encrypted_password
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from field_sales.api.webhooks import receive
from field_sales.connectors import get_connector
from field_sales.connectors.base import (
	PRODUCT_DELETE_TOPIC,
	PRODUCT_UPDATE_TOPIC,
	STOCK_TOPIC,
	ConnectorError,
)
from field_sales.sync.webhooks import process_event

SHOP = "test-shop.myshopify.com"
SECRET = "test-webhook-secret"
LOCATION = "100"
INVENTORY = "5001"


class TestWebhooks(IntegrationTestCase):
	def setUp(self):
		# set_single_value does not validate; everything rolls back after each test.
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": SHOP,
				"shopify_api_version": "2026-10",
				"default_location_id": LOCATION,
				"shopify_client_secret": "*" * len(SECRET),
			},
		)
		set_encrypted_password("Store Connection", "Store Connection", SECRET, "shopify_client_secret")
		self.conn = get_connector()

		self.product = self.make_product("1", inventory_id=INVENTORY, qty=3)
		self.other = self.make_product("2", inventory_id="5002", qty=7)

	def tearDown(self):
		frappe.local.request = None
		# IntegrationTestCase only rolls back once per class: undo this test's rows here.
		# Drop log_sync_durable's re-insert hooks first, or they would commit the test's
		# failure logs right after this rollback.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def make_product(self, variant_id: str, inventory_id: str, qty: float, product_id: str = "900", **values):
		return frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": f"Test product {variant_id}",
				"external_product_id": product_id,
				"external_variant_id": variant_id,
				"external_inventory_id": inventory_id,
				"track_inventory": 1,
				"available_qty": qty,
				"status": "Active",
				**values,
			}
		).insert(ignore_permissions=True)

	def sign(self, body: bytes, secret: str = SECRET) -> str:
		return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

	def headers(self, body: bytes, topic="inventory_levels/update", webhook_id="evt-1", **overrides) -> dict:
		headers = {
			"X-Shopify-Hmac-Sha256": self.sign(body),
			"X-Shopify-Shop-Domain": SHOP,
			"X-Shopify-Topic": topic,
			"X-Shopify-Webhook-Id": webhook_id,
		}
		headers.update(overrides)
		return {k: v for k, v in headers.items() if v is not None}

	def stock_event(self, available=10, location=LOCATION, inventory=INVENTORY, updated_at="2026-10-14T10:00:00Z"):
		return {
			"topic": STOCK_TOPIC,
			"platform_topic": "inventory_levels/update",
			"event_id": frappe.generate_hash(length=10),
			"updated_at": updated_at,
			"payload": {
				"inventory_item_id": int(inventory),
				"location_id": int(location),
				"available": available,
				"updated_at": updated_at,
			},
		}

	def qty(self, doc) -> float:
		return frappe.db.get_value("Platform Product", doc.name, "available_qty")

	def logs(self, **filters):
		return frappe.get_all(
			"Sync Log",
			filters={"operation": "Webhook", **filters},
			fields=["status", "endpoint", "external_id", "error_code", "response_payload"],
		)

	def fake_request(self, body: bytes, headers: dict):
		builder = EnvironBuilder(method="POST", data=body, headers=headers, content_type="application/json")
		frappe.local.request = Request(builder.get_environ())
		frappe.local.response = frappe._dict()

	# -- verify / parse ----------------------------------------------------

	def test_verify_webhook_valid(self):
		body = b'{"a": 1}'
		self.assertTrue(self.conn.verify_webhook(self.headers(body), body))

	def test_verify_webhook_rejects_tampered_body(self):
		body = b'{"a": 1}'
		self.assertFalse(self.conn.verify_webhook(self.headers(body), b'{"a": 2}'))

	def test_verify_webhook_rejects_missing_signature(self):
		body = b"{}"
		self.assertFalse(self.conn.verify_webhook(self.headers(body, **{"X-Shopify-Hmac-Sha256": None}), body))

	def test_verify_webhook_rejects_wrong_shop(self):
		body = b"{}"
		headers = self.headers(body, **{"X-Shopify-Shop-Domain": "other.myshopify.com"})
		self.assertFalse(self.conn.verify_webhook(headers, body))

	def test_verify_webhook_shop_is_case_insensitive(self):
		body = b"{}"
		headers = self.headers(body, **{"X-Shopify-Shop-Domain": SHOP.upper()})
		self.assertTrue(self.conn.verify_webhook(headers, body))

	def test_verify_webhook_rejects_without_secret(self):
		frappe.db.delete("__Auth", {"doctype": "Store Connection", "fieldname": "shopify_client_secret"})
		body = b"{}"
		self.assertFalse(self.conn.verify_webhook(self.headers(body), body))

	def test_parse_webhook_maps_topics(self):
		for platform_topic, topic in (
			("inventory_levels/update", STOCK_TOPIC),
			("products/update", PRODUCT_UPDATE_TOPIC),
			("products/delete", PRODUCT_DELETE_TOPIC),
		):
			body = json.dumps({"id": 1, "updated_at": "2026-10-14T10:00:00Z"}).encode()
			event = self.conn.parse_webhook(self.headers(body, topic=platform_topic, webhook_id="w-9"), body)
			self.assertEqual(event["topic"], topic)
			self.assertEqual(event["platform_topic"], platform_topic)
			self.assertEqual(event["event_id"], "w-9")
			self.assertEqual(event["updated_at"], "2026-10-14T10:00:00Z")
			self.assertEqual(event["payload"]["id"], 1)

	def test_handle_stock_event_normalizes(self):
		result = self.conn.handle_stock_event({"inventory_item_id": 5001, "location_id": 100, "available": None})
		self.assertEqual(
			result, {"external_inventory_id": "5001", "location_id": "100", "available": 0.0}
		)
		self.assertIsNone(self.conn.handle_stock_event({"foo": "bar"}))

	# -- process_event -----------------------------------------------------

	def test_stock_event_updates_only_matching_product(self):
		result = process_event("Shopify", self.stock_event(available=10))

		self.assertEqual(result["updated"], 1)
		self.assertEqual(self.qty(self.product), 10)
		self.assertEqual(self.qty(self.other), 7)
		self.assertIsNotNone(frappe.db.get_value("Platform Product", self.product.name, "stock_updated_at"))
		self.assertEqual([log.status for log in self.logs(endpoint="inventory_levels/update")], ["Success"])

	def test_stock_event_for_location_not_sold_from_is_ignored(self):
		# 999 is not in the Locations table at all; 200 is, but unticked.
		frappe.db.set_single_value("Store Connection", "default_location_id", LOCATION)
		settings = frappe.get_doc("Store Connection")
		settings.append("locations", {"location_id": "200", "location_name": "Warehouse", "sell_from": 0})
		settings.save(ignore_permissions=True)

		for location in ("999", "200"):
			result = process_event("Shopify", self.stock_event(location=location))
			self.assertEqual(result["note"], "Ignored: location not sold from")
		self.assertEqual(self.qty(self.product), 3)

	def test_stock_event_without_matching_product_is_ignored(self):
		result = process_event("Shopify", self.stock_event(inventory="777"))

		self.assertEqual(result["note"], "Ignored: no matching product")
		self.assertEqual(self.logs()[0].status, "Success")

	def test_older_stock_event_is_ignored(self):
		process_event("Shopify", self.stock_event(available=10, updated_at="2026-10-14T10:00:00Z"))
		result = process_event("Shopify", self.stock_event(available=2, updated_at="2026-10-14T09:00:00Z"))

		self.assertEqual(result["note"], "Ignored: older than stored stock")
		self.assertEqual(self.qty(self.product), 10)

	def test_newer_stock_event_wins(self):
		process_event("Shopify", self.stock_event(available=10, updated_at="2026-10-14T10:00:00Z"))
		process_event("Shopify", self.stock_event(available=4, updated_at="2026-10-14T11:00:00Z"))

		self.assertEqual(self.qty(self.product), 4)

	def test_product_delete_archives_all_variants(self):
		sibling = self.make_product("3", inventory_id="5003", qty=1)
		event = {
			"topic": PRODUCT_DELETE_TOPIC,
			"platform_topic": "products/delete",
			"event_id": "del-1",
			"updated_at": None,
			"payload": {"id": 900},
		}
		result = process_event("Shopify", event)

		self.assertEqual(result["archived"], 3)
		for doc in (self.product, self.other, sibling):
			self.assertEqual(frappe.db.get_value("Platform Product", doc.name, "status"), "Archived")
		self.assertEqual(self.logs(external_id="del-1")[0].endpoint, "products/delete")

	def test_product_update_upserts_and_archives_missing_variants(self):
		# Variant 2 (same product) is gone from the store; variant 8 is new.
		kept = {
			"external_product_id": "900",
			"external_variant_id": "1",
			"external_inventory_id": INVENTORY,
			"product_title": "Renamed",
			"variant_title": "",
			"sku": "",
			"barcode": "",
			"price": 5.0,
			"compare_at_price": None,
			"collection": "",
			"image_url": "",
			"status": "Active",
			"track_inventory": True,
		}
		new = {**kept, "external_variant_id": "8", "external_inventory_id": "5008"}
		event = {
			"topic": PRODUCT_UPDATE_TOPIC,
			"platform_topic": "products/update",
			"event_id": "upd-1",
			"updated_at": None,
			"payload": {"id": 900},
		}

		with (
			patch("field_sales.connectors.shopify.ShopifyConnector.get_product", return_value=[kept, new]),
			patch("field_sales.connectors.shopify.ShopifyConnector.get_stock", return_value={INVENTORY: 6.0, "5008": 2.0}),
		):
			result = process_event("Shopify", event)

		self.assertEqual(result["created"], 1)
		self.assertEqual(result["updated"], 1)
		self.assertEqual(result["archived"], 1)
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "product_title"), "Renamed")
		self.assertEqual(self.qty(self.product), 6)
		self.assertEqual(frappe.db.get_value("Platform Product", self.other.name, "status"), "Archived")
		self.assertTrue(frappe.db.exists("Platform Product", "Shopify-8"))

	def test_failure_writes_failed_log_and_reraises(self):
		event = {
			"topic": PRODUCT_UPDATE_TOPIC,
			"platform_topic": "products/update",
			"event_id": "fail-1",
			"updated_at": None,
			"payload": {"id": 900},
		}
		error = ConnectorError("boom", code="HTTP_500")

		with patch("field_sales.connectors.shopify.ShopifyConnector.get_product", side_effect=error):
			with self.assertRaises(ConnectorError):
				process_event("Shopify", event)

		log = self.logs(external_id="fail-1")[0]
		self.assertEqual(log.status, "Failed")
		self.assertEqual(log.error_code, "HTTP_500")
		self.assertEqual(log.endpoint, "products/update")

	# -- product price -----------------------------------------------------

	def store_variant(self, variant_id="1", inventory_id=INVENTORY, price=5.0, compare_at_price=None):
		"""Normalized variant as ``get_product`` returns it (the store's current state)."""
		return {
			"external_product_id": "900",
			"external_variant_id": variant_id,
			"external_inventory_id": inventory_id,
			"product_title": f"Test product {variant_id}",
			"variant_title": "",
			"sku": "",
			"barcode": "",
			"price": price,
			"compare_at_price": compare_at_price,
			"collection": "",
			"image_url": "",
			"status": "Active",
			"track_inventory": True,
		}

	def price_event(self, variants, event_id="price-1"):
		return {
			"topic": PRODUCT_UPDATE_TOPIC,
			"platform_topic": "products/update",
			"event_id": event_id,
			"updated_at": "2026-10-16T10:00:00Z",
			"payload": {"id": 900, "variants": variants},
		}

	def run_price_event(self, event, store_variants):
		with (
			patch("field_sales.connectors.shopify.ShopifyConnector.get_product", return_value=store_variants),
			patch("field_sales.connectors.shopify.ShopifyConnector.get_stock", return_value={INVENTORY: 3.0, "5002": 7.0}),
			patch("field_sales.sync.webhooks.frappe.publish_realtime") as publish,
		):
			result = process_event("Shopify", event)
		return result, [c for c in publish.call_args_list if c.args[0] == "product_price_updated"]

	def test_handle_product_event_normalizes(self):
		prices = self.conn.handle_product_event(
			{
				"variants": [
					{"id": "gid://shopify/ProductVariant/1", "price": "12.50", "compare_at_price": "15.00"},
					{"id": 2, "price": "9", "compare_at_price": None},
				]
			}
		)
		self.assertEqual(
			prices,
			[
				{"external_variant_id": "1", "price": 12.5, "compare_at_price": 15.0},
				{"external_variant_id": "2", "price": 9.0, "compare_at_price": None},
			],
		)
		self.assertIsNone(self.conn.handle_product_event({"id": 900}))

	def test_handle_product_event_rejects_variant_without_price(self):
		with self.assertRaises(ConnectorError) as raised:
			self.conn.handle_product_event({"variants": [{"id": 1}]})
		self.assertEqual(raised.exception.code, "BAD_PAYLOAD")

	def test_price_change_is_written_and_published(self):
		event = self.price_event([{"id": 1, "price": "12.50", "compare_at_price": "15.00"}, {"id": 2, "price": "5.00"}])
		result, published = self.run_price_event(
			event, [self.store_variant("1", price=12.5, compare_at_price=15.0), self.store_variant("2", "5002")]
		)

		self.product.reload()
		self.assertEqual((self.product.price, self.product.compare_at_price), (12.5, 15.0))
		self.assertIsNotNone(self.product.last_synced)
		self.assertEqual(result["price_changed"], 2)
		messages = {c.args[1]["product"]: c.args[1] for c in published}
		self.assertEqual(messages[self.product.name], {"product": self.product.name, "price": 12.5, "compare_at_price": 15.0})
		self.assertEqual(published[0].kwargs, {"doctype": "Platform Product", "after_commit": True})

	def test_unchanged_price_is_not_written_or_published(self):
		frappe.db.set_value("Platform Product", self.product.name, {"price": 5, "compare_at_price": 0, "last_synced": None})
		with patch("field_sales.sync.webhooks.frappe.db.set_value") as set_value:
			from field_sales.sync.webhooks import _apply_prices

			_apply_prices("Shopify", [{"external_variant_id": "1", "price": 5.0, "compare_at_price": None}])
		set_value.assert_not_called()

	def test_stale_payload_price_is_corrected_by_refetch(self):
		# The payload says 7, but the store now says 5 (a newer change already happened).
		frappe.db.set_value("Platform Product", self.product.name, "price", 5)
		result, published = self.run_price_event(
			self.price_event([{"id": 1, "price": "7.00"}]), [self.store_variant("1", price=5.0)]
		)
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "price"), 5)
		self.assertEqual(published, [])
		self.assertEqual(result["price_changed"], 0)

	def test_price_for_unknown_variant_is_ignored(self):
		result, published = self.run_price_event(
			self.price_event([{"id": 999, "price": "1.00"}]),
			[self.store_variant("1", price=0), self.store_variant("2", "5002", price=0)],
		)
		self.assertFalse(frappe.db.exists("Platform Product", {"external_variant_id": "999"}))
		self.assertEqual(frappe.db.get_value("Platform Product", self.product.name, "price"), 0)
		self.assertEqual(published, [])
		self.assertEqual(result["price_changed"], 0)

	def test_bad_product_payload_writes_failed_log(self):
		with self.assertRaises(ConnectorError):
			process_event("Shopify", self.price_event([{"id": 1}], event_id="bad-price"))

		log = self.logs(external_id="bad-price")[0]
		self.assertEqual(log.status, "Failed")
		self.assertEqual(log.error_code, "BAD_PAYLOAD")
		self.assertEqual(log.endpoint, "products/update")

	# -- receive -----------------------------------------------------------

	def test_receive_rejects_bad_signature(self):
		frappe.cache.delete(frappe.cache.make_key("field_sales:webhook_invalid_logged"))
		body = b'{"inventory_item_id": 5001}'
		self.fake_request(body, self.headers(body, **{"X-Shopify-Hmac-Sha256": "nope"}))

		with patch("frappe.enqueue") as enqueue:
			receive()

		self.assertEqual(frappe.local.response.http_status_code, 401)
		enqueue.assert_not_called()
		self.assertEqual(self.logs(error_code="INVALID_SIGNATURE")[0].status, "Failed")

	def test_receive_enqueues_valid_event_once(self):
		body = json.dumps(
			{"inventory_item_id": 5001, "location_id": 100, "available": 4, "updated_at": "2026-10-14T10:00:00Z"}
		).encode()
		event_id = frappe.generate_hash(length=12)
		self.fake_request(body, self.headers(body, webhook_id=event_id))

		with patch("frappe.enqueue") as enqueue:
			first = receive()
			self.assertEqual(frappe.local.response.http_status_code, 200)
			self.assertTrue(first["ok"])
			enqueue.assert_called_once()
			kwargs = enqueue.call_args.kwargs
			self.assertEqual(enqueue.call_args.args[0], "field_sales.sync.webhooks.process_event")
			self.assertEqual(kwargs["platform"], "Shopify")
			self.assertEqual(kwargs["webhook"]["topic"], STOCK_TOPIC)
			self.assertEqual(kwargs["webhook"]["event_id"], event_id)
			self.assertNotIn("event", kwargs)  # reserved by frappe.enqueue

			# Same delivery again: acknowledged, not queued twice.
			self.fake_request(body, self.headers(body, webhook_id=event_id))
			second = receive()
			self.assertEqual(second["message"], "duplicate")
			enqueue.assert_called_once()

	def test_receive_job_kwargs_reach_process_event(self):
		"""Run the real frappe.enqueue (inline) so reserved-kwarg clashes show up."""
		body = json.dumps(
			{"inventory_item_id": 5001, "location_id": 100, "available": 4, "updated_at": "2026-10-14T10:00:00Z"}
		).encode()
		event_id = frappe.generate_hash(length=12)
		self.fake_request(body, self.headers(body, webhook_id=event_id))
		real_enqueue = frappe.enqueue

		def run_inline(*args, **kwargs):
			return real_enqueue(*args, now=True, **kwargs)

		with (
			patch("frappe.enqueue", side_effect=run_inline),
			patch("field_sales.sync.webhooks.process_event") as job,
		):
			receive()

		job.assert_called_once()
		self.assertEqual(job.call_args.kwargs["platform"], "Shopify")
		self.assertEqual(job.call_args.kwargs["webhook"]["event_id"], event_id)

	def test_receive_ignores_when_disabled(self):
		frappe.db.set_single_value("Store Connection", "enabled", 0)
		body = b"{}"
		self.fake_request(body, self.headers(body))

		with patch("frappe.enqueue") as enqueue:
			receive()

		self.assertEqual(frappe.local.response.http_status_code, 200)
		enqueue.assert_not_called()
