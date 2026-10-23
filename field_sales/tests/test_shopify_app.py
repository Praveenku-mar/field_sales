# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Shopify App Store pieces: OAuth install, expiring token refresh, app and compliance webhooks."""

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlparse

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime, nowdate
from frappe.utils.password import get_decrypted_password, set_encrypted_password
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from field_sales.connectors import get_connector
from field_sales.connectors.base import ConnectorError
from field_sales.connectors.shopify import ShopifyConnector
from field_sales.shopify import oauth, privacy, webhooks

SHOP = "dev-shop.myshopify.com"
APP_ID = "app-client-id"
APP_SECRET = "app-client-secret"
CC_SECRET = "cc-client-secret"
APP_CONF = {
	"shopify_app_client_id": APP_ID,
	"shopify_app_client_secret": APP_SECRET,
	"shopify_app_url": "https://fs.example.com",
}


def _response(status=200, body=None):
	response = MagicMock()
	response.status_code = status
	response.ok = status < 400
	response.json.return_value = body
	response.text = json.dumps(body)
	response.reason = "x"
	return response


def _token_body(token="shpat_new", refresh="shprt_new"):
	return {
		"access_token": token,
		"scope": "write_products,write_orders",
		"expires_in": 3600,
		"refresh_token": refresh,
		"refresh_token_expires_in": 7776000,
	}


class ShopifyAppTestCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 0,
				"store_url": "",
				"shopify_auth_mode": "Client Credentials",
				"shopify_client_id": "",
				"shopify_api_version": "2026-10",
				"installed_on": None,
				"uninstalled_on": None,
				"token_expiry": None,
				"refresh_token_expiry": None,
				"match_shop_settings": 0,
			},
		)
		for field in ("access_token", "refresh_token", "shopify_client_secret"):
			frappe.db.delete("__Auth", {"doctype": "Store Connection", "fieldname": field})

		for target in (
			patch.dict(frappe.local.conf, APP_CONF),
			# The endpoints commit / roll back their own work; keep the test's transaction instead.
			patch.object(frappe.db, "commit"),
			patch.object(frappe.db, "rollback"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.cache.delete_keys("field_sales:shopify")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		frappe.db.after_rollback.reset()
		patch.stopall()
		frappe.db.rollback()

	def connect_oauth(
		self, enabled=1, token="shpat_old", expiry_minutes=60, refresh="shprt_old", refresh_days=90
	):
		frappe.db.set_single_value(
			"Store Connection",
			{
				"enabled": enabled,
				"store_url": SHOP,
				"shopify_auth_mode": "OAuth",
				"installed_on": add_to_date(now_datetime(), days=-1),
				"access_token": "*****" if token else None,
				"token_expiry": add_to_date(now_datetime(), minutes=expiry_minutes) if token else None,
				"refresh_token": "*****" if refresh else None,
				"refresh_token_expiry": add_to_date(now_datetime(), days=refresh_days) if refresh else None,
			},
		)
		if token:
			set_encrypted_password("Store Connection", "Store Connection", token, "access_token")
		if refresh:
			set_encrypted_password("Store Connection", "Store Connection", refresh, "refresh_token")

	def settings(self):
		return frappe.get_single("Store Connection")

	def password(self, field):
		return get_decrypted_password("Store Connection", "Store Connection", field, raise_exception=False)


# ----------------------------------------------------------------------
# OAuth install
# ----------------------------------------------------------------------


class TestOAuthInstall(ShopifyAppTestCase):
	def signed(self, params, secret=APP_SECRET):
		message = "&".join(f"{k}={params[k]}" for k in sorted(params))
		return {**params, "hmac": hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()}

	def get(self, fn, params):
		builder = EnvironBuilder(method="GET", query_string=urlencode(params))
		frappe.local.request = Request(builder.get_environ())
		frappe.local.response = frappe._dict()
		return fn(**params)

	def install(self, shop=SHOP, secret=APP_SECRET):
		return self.get(oauth.install, self.signed({"shop": shop, "timestamp": "1792000000"}, secret))

	def state_from(self, response):
		query = parse_qs(urlparse(response.headers["Location"]).query)
		return query["state"][0], query

	def callback(self, state, code="auth-code", shop=SHOP):
		params = self.signed({"code": code, "shop": shop, "state": state, "timestamp": "1792000001"})
		with (
			patch.object(oauth.requests, "post", return_value=_response(200, _token_body())) as post,
			patch.object(oauth.frappe, "enqueue") as enqueue,
		):
			response = self.get(oauth.callback, params)
		return response, post, enqueue

	def test_install_redirects_to_authorize_with_state(self):
		response = self.install()
		self.assertEqual(response.status_code, 302)
		location = urlparse(response.headers["Location"])
		self.assertEqual((location.netloc, location.path), (SHOP, "/admin/oauth/authorize"))
		state, query = self.state_from(response)
		self.assertEqual(query["client_id"], [APP_ID])
		self.assertEqual(
			query["redirect_uri"], ["https://fs.example.com/api/method/field_sales.shopify.oauth.callback"]
		)
		self.assertIn("read_products", query["scope"][0])
		self.assertEqual(frappe.cache.get(oauth._state_key(state)), SHOP.encode())

	def test_install_rejects_bad_hmac(self):
		response = self.install(secret="wrong")
		self.assertEqual(response.status_code, 400)
		self.assertIn(b"Invalid signature", response.data)

	def test_install_rejects_bad_shop_domain(self):
		for shop in ("evil.example.com", "dev-shop.myshopify.com.evil.com", "-x.myshopify.com"):
			response = self.install(shop=shop)
			self.assertEqual(response.status_code, 400, shop)
			self.assertIn(b"Invalid shop domain", response.data)

	def test_install_refuses_another_shop_while_connected(self):
		self.connect_oauth()
		frappe.db.set_single_value("Store Connection", "store_url", "other.myshopify.com")
		response = self.install()
		self.assertEqual(response.status_code, 400)

	def test_install_without_app_config_is_rejected(self):
		with patch.dict(frappe.local.conf, {"shopify_app_client_secret": ""}):
			self.assertEqual(self.install().status_code, 400)

	def test_callback_stores_tokens_and_enables_connection(self):
		state, _query = self.state_from(self.install())
		response, post, enqueue = self.callback(state)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers["Location"], "/fieldsales")
		sent = post.call_args.kwargs["data"]
		self.assertEqual(post.call_args.args[0], f"https://{SHOP}/admin/oauth/access_token")
		self.assertEqual((sent["code"], sent["expiring"], sent["client_id"]), ("auth-code", "1", APP_ID))

		settings = self.settings()
		self.assertEqual(
			(settings.enabled, settings.shopify_auth_mode, settings.store_url, settings.platform),
			(1, "OAuth", SHOP, "Shopify"),
		)
		self.assertEqual(self.password("access_token"), "shpat_new")
		self.assertEqual(self.password("refresh_token"), "shprt_new")
		self.assertEqual(settings.shopify_scopes, "write_products,write_orders")
		self.assertTrue(settings.installed_on and settings.token_expiry and settings.refresh_token_expiry)
		self.assertFalse(settings.uninstalled_on)
		self.assertGreater(get_datetime(settings.refresh_token_expiry), add_to_date(now_datetime(), days=89))
		self.assertEqual(enqueue.call_args.args[0], "field_sales.shopify.webhooks.register_webhooks")
		self.assertEqual(frappe.session.user, "Administrator")  # restored

	def test_reinstall_overwrites_old_tokens(self):
		self.connect_oauth(enabled=0)
		frappe.db.set_single_value("Store Connection", "uninstalled_on", now_datetime())
		state, _query = self.state_from(self.install())
		self.callback(state)
		self.assertEqual(self.password("access_token"), "shpat_new")
		self.assertEqual(self.password("refresh_token"), "shprt_new")
		self.assertFalse(self.settings().uninstalled_on)

	def test_state_cannot_be_reused(self):
		state, _query = self.state_from(self.install())
		first, _post, _enqueue = self.callback(state)
		second, post, _enqueue = self.callback(state)
		self.assertEqual(first.status_code, 302)
		self.assertEqual(second.status_code, 400)
		post.assert_not_called()

	def test_callback_rejects_unknown_state_and_bad_hmac(self):
		response, post, _enqueue = self.callback("made-up-state")
		self.assertEqual(response.status_code, 400)
		post.assert_not_called()

		state, _query = self.state_from(self.install())
		params = {"code": "c", "shop": SHOP, "state": state, "timestamp": "1", "hmac": "00" * 32}
		self.assertEqual(self.get(oauth.callback, params).status_code, 400)

	def test_failures_are_logged_without_hmac(self):
		self.install(secret="wrong")
		log = frappe.get_last_doc("Sync Log", filters={"operation": "Install"})
		self.assertEqual((log.status, log.error_code), ("Failed", "INSTALL_REJECTED"))
		self.assertNotIn("hmac", (log.request_payload or "") + (log.error_message or ""))


# ----------------------------------------------------------------------
# Tokens
# ----------------------------------------------------------------------


class TestTokens(ShopifyAppTestCase):
	def test_valid_oauth_token_is_reused_without_a_request(self):
		self.connect_oauth()
		with patch("field_sales.connectors.shopify.requests.post") as post:
			self.assertEqual(get_connector()._get_token(), "shpat_old")
		post.assert_not_called()

	def test_oauth_mode_uses_site_config_api_version(self):
		self.connect_oauth()
		with patch.dict(frappe.local.conf, {"shopify_app_api_version": "2026-07"}):
			self.assertIn("/admin/api/2026-07/", get_connector().graphql_url)

	def test_refresh_stores_new_tokens(self):
		self.connect_oauth(expiry_minutes=1)  # inside the refresh margin
		with patch(
			"field_sales.connectors.shopify.requests.post", return_value=_response(200, _token_body())
		) as post:
			token = get_connector()._get_token()

		self.assertEqual(token, "shpat_new")
		sent = post.call_args.kwargs["data"]
		self.assertEqual(
			(sent["grant_type"], sent["refresh_token"], sent["client_id"], sent["client_secret"]),
			("refresh_token", "shprt_old", APP_ID, APP_SECRET),
		)
		self.assertEqual(self.password("access_token"), "shpat_new")
		self.assertEqual(self.password("refresh_token"), "shprt_new")
		log = frappe.get_last_doc("Sync Log", filters={"operation": "Token Refresh"})
		self.assertNotIn("shprt_new", log.response_payload)
		self.assertNotIn("shpat_new", log.response_payload)

	def test_waiting_worker_uses_token_refreshed_by_another(self):
		self.connect_oauth(expiry_minutes=1)
		conn = get_connector()
		# Another worker refreshed after this instance read the old token.
		set_encrypted_password("Store Connection", "Store Connection", "shpat_other", "access_token")
		frappe.db.set_single_value("Store Connection", "token_expiry", add_to_date(now_datetime(), hours=1))
		with patch("field_sales.connectors.shopify.requests.post") as post:
			self.assertEqual(conn._refresh_oauth_token(stale_token="shpat_old"), "shpat_other")
		post.assert_not_called()

	def test_expired_refresh_token_requires_reauth(self):
		self.connect_oauth(enabled=1, expiry_minutes=-5, refresh_days=-1)
		with patch("field_sales.connectors.shopify.requests.post") as post:
			with self.assertRaises(ConnectorError) as ctx:
				get_connector()._get_token()
		post.assert_not_called()
		self.assertEqual(ctx.exception.code, "REAUTH_REQUIRED")
		settings = self.settings()
		self.assertEqual(settings.enabled, 0)
		self.assertIn("Reinstall required", settings.last_test_status)
		self.assertFalse(self.password("refresh_token"))

	def test_rejected_refresh_token_requires_reauth(self):
		self.connect_oauth(expiry_minutes=-5)
		with patch(
			"field_sales.connectors.shopify.requests.post",
			return_value=_response(401, {"error": "invalid_grant"}),
		):
			with self.assertRaises(ConnectorError) as ctx:
				get_connector()._get_token()
		self.assertEqual(ctx.exception.code, "REAUTH_REQUIRED")
		self.assertEqual(self.settings().enabled, 0)

	def test_server_error_on_refresh_does_not_disable(self):
		self.connect_oauth(expiry_minutes=-5)
		with patch("field_sales.connectors.shopify.requests.post", return_value=_response(503, {})):
			with self.assertRaises(ConnectorError) as ctx:
				get_connector()._get_token()
		self.assertEqual(ctx.exception.code, "AUTH_FAILED")
		self.assertEqual(self.settings().enabled, 1)

	def test_401_forces_one_refresh_then_fails(self):
		self.connect_oauth()
		graphql_401 = _response(401, {"errors": "Unauthorized"})
		with patch(
			"field_sales.connectors.shopify.requests.post",
			side_effect=[graphql_401, _response(200, _token_body()), graphql_401],
		) as post:
			with self.assertRaises(ConnectorError):
				get_connector()._graphql("{ shop { name } }")
		self.assertEqual(post.call_count, 3)  # query, refresh, query again; no second refresh
		self.assertEqual(post.call_args_list[1].kwargs["data"]["grant_type"], "refresh_token")

	def test_client_credentials_mode_unchanged(self):
		frappe.db.set_single_value(
			"Store Connection",
			{"enabled": 1, "store_url": SHOP, "shopify_client_id": "cc-id", "shopify_client_secret": "*****"},
		)
		set_encrypted_password("Store Connection", "Store Connection", CC_SECRET, "shopify_client_secret")
		with patch(
			"field_sales.connectors.shopify.requests.post",
			return_value=_response(200, {"access_token": "shpat_cc", "expires_in": 86399}),
		) as post:
			conn = get_connector()
			self.assertFalse(conn.oauth)
			self.assertIn("/admin/api/2026-10/", conn.graphql_url)
			self.assertEqual(conn._get_token(), "shpat_cc")
		sent = post.call_args.kwargs["data"]
		self.assertEqual(
			(sent["grant_type"], sent["client_id"], sent["client_secret"]),
			("client_credentials", "cc-id", CC_SECRET),
		)
		self.assertEqual(self.password("access_token"), "shpat_cc")

	def test_normalize_store_url_goes_through_connector(self):
		self.assertEqual(
			ShopifyConnector.normalize_store_url("https://admin.shopify.com/store/abc/products"),
			"abc.myshopify.com",
		)
		with self.assertRaises(frappe.ValidationError):
			ShopifyConnector.normalize_store_url("shop.example.com")
		settings = self.settings()
		settings.store_url = " https://Foo.myshopify.com/admin "
		settings.save()
		self.assertEqual(self.settings().store_url, "foo.myshopify.com")

	def test_store_url_is_kept_in_oauth_mode(self):
		self.connect_oauth()
		settings = self.settings()
		settings.store_url = "typed.myshopify.com"
		settings.save()
		self.assertEqual(self.settings().store_url, SHOP)
		self.assertEqual(self.password("access_token"), "shpat_old")  # an unrelated save keeps tokens


# ----------------------------------------------------------------------
# Webhooks
# ----------------------------------------------------------------------


class TestAppWebhooks(ShopifyAppTestCase):
	def setUp(self):
		super().setUp()
		self.connect_oauth()
		enqueue = patch.object(webhooks.frappe, "enqueue")
		self.enqueue = enqueue.start()
		self.addCleanup(enqueue.stop)

	def post(self, topic, body, webhook_id="wh-1", secret=APP_SECRET, shop=SHOP, signature=None, **headers):
		raw = json.dumps(body).encode()
		if signature is None:
			signature = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
		builder = EnvironBuilder(
			method="POST",
			data=raw,
			content_type="application/json",
			headers={
				"X-Shopify-Hmac-Sha256": signature,
				"X-Shopify-Topic": topic,
				"X-Shopify-Shop-Domain": shop,
				"X-Shopify-Webhook-Id": webhook_id,
				**headers,
			},
		)
		frappe.local.request = Request(builder.get_environ())
		frappe.local.response = frappe._dict()
		result = webhooks.receive()
		return frappe.local.response.get("http_status_code") or 200, result

	def test_valid_signature_is_accepted_and_queued(self):
		status, result = self.post("app/uninstalled", {"id": 1, "domain": SHOP})
		self.assertEqual((status, result["message"]), (200, "ok"))
		self.assertEqual(self.enqueue.call_args.args[0], "field_sales.shopify.webhooks.handle_uninstalled")
		self.assertEqual(self.enqueue.call_args.kwargs["queue"], "short")

	def test_invalid_signature_is_401(self):
		self.assertEqual(self.post("app/uninstalled", {}, secret="wrong")[0], 401)
		self.assertEqual(self.post("app/uninstalled", {}, signature="")[0], 401)
		self.enqueue.assert_not_called()

	def test_client_credentials_secret_is_accepted(self):
		set_encrypted_password("Store Connection", "Store Connection", CC_SECRET, "shopify_client_secret")
		self.assertEqual(self.post("app/uninstalled", {}, secret=CC_SECRET)[0], 200)

	def test_other_shop_is_ignored_with_200(self):
		status, result = self.post("app/uninstalled", {}, shop="other.myshopify.com")
		self.assertEqual((status, result["message"]), (200, "ignored"))
		self.enqueue.assert_not_called()

	def test_duplicate_webhook_id_is_ignored(self):
		self.post("app/uninstalled", {}, webhook_id="wh-dup")
		status, result = self.post("app/uninstalled", {}, webhook_id="wh-dup")
		self.assertEqual((status, result["message"]), (200, "duplicate"))
		self.assertEqual(self.enqueue.call_count, 1)

	def test_compliance_topic_records_request_without_personal_data(self):
		body = {
			"shop_domain": SHOP,
			"customer": {"id": 777, "email": "c@example.com", "phone": "555"},
			"orders_requested": [1],
			"data_request": {"id": 99},
		}
		self.post("customers/data_request", body, webhook_id="wh-dr")
		request = frappe.get_last_doc("Privacy Request")
		self.assertEqual(
			(request.topic, request.shopify_customer_id, request.request_id, request.status),
			("customers/data_request", "777", "99", "Received"),
		)
		self.assertNotIn("c@example.com", frappe.as_json(request.as_dict()))
		self.assertEqual(self.enqueue.call_args.kwargs["privacy_request"], request.name)

	def test_uninstall_clears_tokens_and_disables(self):
		webhooks.handle_uninstalled(SHOP, triggered_at=now_datetime().isoformat())
		settings = self.settings()
		self.assertEqual(settings.enabled, 0)
		self.assertTrue(settings.uninstalled_on)
		self.assertFalse(self.password("access_token"))
		self.assertFalse(self.password("refresh_token"))
		with self.assertRaises(frappe.ValidationError):
			get_connector()  # scheduled jobs stop at the enabled check

	def test_uninstall_older_than_reinstall_is_ignored(self):
		webhooks.handle_uninstalled(SHOP, triggered_at="2020-01-01T00:00:00Z")
		self.assertEqual(self.settings().enabled, 1)
		self.assertEqual(self.password("access_token"), "shpat_old")

	def test_register_webhooks_creates_only_missing(self):
		uri = "https://fs.example.com/api/method/field_sales.shopify.webhooks.receive"
		calls = []

		def fake_graphql(conn, query, variables=None, **kwargs):
			calls.append(query)
			if "webhookSubscriptions(" in query:
				return {"webhookSubscriptions": {"nodes": []}}
			return {"webhookSubscriptionCreate": {"webhookSubscription": {"id": "1"}}}

		with patch.object(ShopifyConnector, "_graphql", fake_graphql):
			self.assertEqual(webhooks.register_webhooks(), {"APP_UNINSTALLED": "created"})

		def existing(conn, query, variables=None, **kwargs):
			return {"webhookSubscriptions": {"nodes": [{"id": "1", "topic": "APP_UNINSTALLED", "uri": uri}]}}

		with patch.object(ShopifyConnector, "_graphql", existing):
			self.assertEqual(webhooks.register_webhooks(), {"APP_UNINSTALLED": "exists"})
		self.assertEqual(len(calls), 2)


# ----------------------------------------------------------------------
# Privacy handlers
# ----------------------------------------------------------------------


class TestPrivacy(ShopifyAppTestCase):
	def setUp(self):
		super().setUp()
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Privacy Stores",
				"owner_name": "Asha",
				"phone": "98765 43210",
				"email": "asha@example.com",
				"address_line1": "1 Market Street",
				"city": "Salem",
				"latitude": 11.6,
				"longitude": 78.1,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Retailer", self.retailer.name, "external_customer_id", "777")
		self.other = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Untouched Stores",
				"phone": "98765 00000",
				"address_line1": "2 Market Street",
				"city": "Salem",
			}
		).insert(ignore_permissions=True)
		self.product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Privacy product",
				"external_product_id": "9700",
				"external_variant_id": "97",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "privacy.rep@example.com",
				"first_name": "P",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		rep = frappe.get_doc({"doctype": "Sales Rep", "user": user.name}).insert(ignore_permissions=True)
		self.order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer.name,
				"sales_rep": rep.name,
				"order_date": nowdate(),
				"remarks": "Call Asha on 98765 43210",
				"items": [{"product": self.product.name, "qty": 2, "rate": 100}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Field Order",
			self.order.name,
			{"docstatus": 1, "external_order_id": "5001", "sync_status": "Synced"},
		)

	def request(self, topic, customer_id="777"):
		return frappe.get_doc(
			{
				"doctype": "Privacy Request",
				"topic": topic,
				"shop_domain": SHOP,
				"shopify_customer_id": customer_id,
				"received_on": now_datetime(),
			}
		).insert(ignore_permissions=True)

	def test_data_request_creates_private_export_and_emails(self):
		request = self.request("customers/data_request")
		with (
			patch.object(privacy.frappe, "sendmail") as sendmail,
			patch.object(privacy, "users_with_roles", return_value=["sm@example.com"]),
		):
			privacy.process(request.name, {"customer": {"id": 777}, "orders_requested": [5001]})

		request.reload()
		self.assertEqual(request.status, "Completed")
		self.assertTrue(request.completed_on)
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Privacy Request", "attached_to_name": request.name},
			fields=["name", "is_private"],
		)
		self.assertEqual(len(files), 1)
		self.assertEqual(files[0].is_private, 1)
		export = json.loads(frappe.get_doc("File", files[0].name).get_content())
		self.assertEqual([r["name"] for r in export["retailers"]], [self.retailer.name])
		self.assertEqual(export["retailers"][0]["email"], "asha@example.com")
		self.assertEqual(export["field_orders"][0]["name"], self.order.name)
		self.assertEqual(export["field_orders"][0]["items"][0]["qty"], 2)
		self.assertEqual(sendmail.call_args.kwargs["recipients"], ["sm@example.com"])
		self.assertNotIn("asha@example.com", request.details)

	def test_customer_redact_anonymises_and_keeps_amounts(self):
		total = frappe.db.get_value("Field Order", self.order.name, "grand_total")
		privacy.process(
			self.request("customers/redact").name, {"customer": {"id": 777}, "orders_to_redact": [5001]}
		)

		retailer = frappe.db.get_value(
			"Retailer",
			self.retailer.name,
			["retailer_name", "owner_name", "email", "phone", "address_line1", "latitude", "status"],
			as_dict=True,
		)
		self.assertTrue(retailer.retailer_name.startswith("Redacted"))
		self.assertFalse(retailer.owner_name or retailer.email or retailer.latitude)
		self.assertEqual(
			(retailer.phone, retailer.address_line1, retailer.status), ("0000000000", "Redacted", "Inactive")
		)
		order = frappe.db.get_value(
			"Field Order",
			self.order.name,
			["retailer_name", "remarks", "grand_total", "order_date"],
			as_dict=True,
		)
		self.assertTrue(order.retailer_name.startswith("Redacted"))
		self.assertFalse(order.remarks)
		self.assertEqual(order.grand_total, total)
		self.assertEqual(
			frappe.db.get_value("Retailer", self.other.name, "retailer_name"), "Untouched Stores"
		)
		self.assertFalse(
			frappe.db.exists("Version", {"ref_doctype": "Retailer", "docname": self.retailer.name})
		)
		log = frappe.get_last_doc("Sync Log", filters={"operation": "Privacy Request"})
		self.assertEqual(log.status, "Success")

	def test_shop_redact_removes_store_data(self):
		self.connect_oauth()
		total = frappe.db.get_value("Field Order", self.order.name, "grand_total")
		privacy.process(self.request("shop/redact", customer_id=None).name, {"shop_domain": SHOP})

		self.assertTrue(
			frappe.db.get_value("Retailer", self.other.name, "retailer_name").startswith("Redacted")
		)
		self.assertFalse(frappe.db.exists("Platform Product", self.product.name))
		self.assertFalse(frappe.db.count("Sync Log", {"request_payload": ("is", "set")}))
		self.assertFalse(self.password("access_token"))
		self.assertFalse(self.password("refresh_token"))
		self.assertEqual(self.settings().enabled, 0)
		self.assertEqual(frappe.db.get_value("Field Order", self.order.name, "grand_total"), total)

	def test_failure_marks_request_failed(self):
		request = self.request("customers/redact")
		with patch.object(privacy, "_redact", side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				privacy.process(request.name, {"customer": {"id": 777}})
		self.assertEqual(frappe.db.get_value("Privacy Request", request.name, "status"), "Failed")
