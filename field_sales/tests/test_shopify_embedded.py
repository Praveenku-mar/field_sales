# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Field Sales embedded in the Shopify admin: session tokens, token exchange, the embedded page."""

import time
from unittest.mock import patch

import frappe
import jwt
from frappe.utils.password import get_decrypted_password
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request, Response

from field_sales.shopify import embedded, oauth, session_token
from field_sales.tests.test_shopify_app import (
	APP_ID,
	APP_SECRET,
	SHOP,
	ShopifyAppTestCase,
	_response,
	_token_body,
)
from field_sales.www import fieldsales


def make_token(shop=SHOP, secret=APP_SECRET, aud=APP_ID, exp_in=60, **overrides):
	now = int(time.time())
	claims = {
		"iss": f"https://{shop}/admin",
		"dest": f"https://{shop}",
		"aud": aud,
		"sub": "42",
		"exp": now + exp_in,
		"nbf": now - 5,
		"iat": now - 5,
		"jti": "f8912129-1af6-4cad-9ca3-76b0f7621087",
		"sid": "aaea182f2732d44c23057c0fea584021a4485b2bd25d3eb7fd349313ad24c685",
		**overrides,
	}
	return jwt.encode(claims, secret, algorithm="HS256")


class EmbeddedTestCase(ShopifyAppTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Store Connection", "embedded_user", None)
		frappe.local.response_headers = frappe.local.response_headers.__class__()
		enqueue = patch.object(oauth.frappe, "enqueue")
		self.enqueue = enqueue.start()
		self.addCleanup(enqueue.stop)

	def request(
		self, path="/api/method/field_sales.frontend_api.get_session", query="", token=None, method="GET"
	):
		headers = {"Authorization": f"Bearer {token}"} if token else {}
		builder = EnvironBuilder(path=path, query_string=query, method=method, headers=headers)
		frappe.local.request = Request(builder.get_environ())
		frappe.local.response = frappe._dict()
		frappe.local.shopify_frame_shop = None  # a real request starts with a fresh frappe.local
		frappe.set_user("Guest")

	def authenticate(self, token):
		self.request(token=token)
		session_token.authenticate()
		return frappe.session.user

	def assert_retry_401(self, token):
		with self.assertRaises(frappe.AuthenticationError):
			self.authenticate(token)
		self.assertEqual(frappe.local.response_headers.get(session_token.RETRY_HEADER), "1")
		frappe.local.response_headers = frappe.local.response_headers.__class__()


class TestSessionToken(EmbeddedTestCase):
	def setUp(self):
		super().setUp()
		self.connect_oauth()

	def test_valid_token_decodes(self):
		claims = session_token.decode(make_token())
		self.assertEqual((claims["shop"], claims["sub"]), (SHOP, "42"))

	def test_invalid_tokens_get_401_with_retry_header(self):
		for token in (
			make_token(secret="wrong-secret"),
			make_token(exp_in=-60),  # past the 10 s leeway
			make_token(aud="another-app"),
			make_token(shop="other-shop.myshopify.com"),
			make_token(iss="https://other-shop.myshopify.com/admin"),
		):
			self.assert_retry_401(token)
			self.assertEqual(frappe.session.user, "Guest")

	def test_small_clock_skew_is_tolerated(self):
		self.assertEqual(session_token.decode(make_token(exp_in=-5))["shop"], SHOP)

	def test_client_credentials_site_rejects_tokens(self):
		frappe.db.set_single_value("Store Connection", "shopify_auth_mode", "Client Credentials")
		self.assert_retry_401(make_token())

	def test_other_requests_are_left_alone(self):
		self.request(token="not-a-jwt")
		session_token.authenticate()  # a Frappe OAuth bearer or junk: Frappe's own checks decide
		self.assertEqual(frappe.session.user, "Guest")
		self.request()
		session_token.authenticate()
		self.assertEqual(frappe.session.user, "Guest")

	def test_auth_hook_signs_in_created_embedded_user(self):
		user = self.authenticate(make_token())
		expected = f"shopify-admin@{frappe.local.site}"
		self.assertEqual(user, expected)
		self.assertIn("Sales Manager", frappe.get_roles(expected))
		self.assertTrue(frappe.db.exists("Sales Rep", {"user": expected}))
		self.assertEqual(frappe.db.get_single_value("Store Connection", "embedded_user"), expected)
		self.assertEqual(frappe.local.shopify_staff_id, "42")
		# Second request: same user, nothing created again.
		self.assertEqual(self.authenticate(make_token()), expected)

	def test_auth_hook_keeps_request_arguments(self):
		# The first request also creates the embedded user (more set_user calls inside).
		self.request(token=make_token())
		frappe.local.form_dict = frappe._dict(doctype="Retailer", key="open_orders")
		session_token.authenticate()
		self.assertEqual(frappe.local.form_dict, {"doctype": "Retailer", "key": "open_orders"})

	def test_auth_hook_uses_configured_embedded_user(self):
		frappe.db.set_single_value("Store Connection", "embedded_user", "Administrator")
		self.assertEqual(self.authenticate(make_token()), "Administrator")

	def test_cookie_session_wins(self):
		self.request(token=make_token(secret="wrong"))
		frappe.set_user("Administrator")  # signed in by cookie before the hook runs
		session_token.authenticate()
		self.assertEqual(frappe.session.user, "Administrator")


class TestTokenExchange(EmbeddedTestCase):
	def test_first_request_exchanges_and_saves_tokens(self):
		token = make_token()
		with patch.object(embedded.requests, "post", return_value=_response(200, _token_body())) as post:
			user = self.authenticate(token)

		self.assertEqual(user, f"shopify-admin@{frappe.local.site}")
		sent = post.call_args.kwargs["data"]
		self.assertEqual(post.call_args.args[0], f"https://{SHOP}/admin/oauth/access_token")
		self.assertEqual(
			(
				sent["grant_type"],
				sent["subject_token"],
				sent["subject_token_type"],
				sent["requested_token_type"],
			),
			(embedded.TOKEN_EXCHANGE_GRANT, token, embedded.ID_TOKEN_TYPE, embedded.OFFLINE_TOKEN_TYPE),
		)
		self.assertEqual(sent["expiring"], "1")
		settings = self.settings()
		self.assertEqual(
			(settings.enabled, settings.shopify_auth_mode, settings.store_url), (1, "OAuth", SHOP)
		)
		self.assertEqual(
			get_decrypted_password("Store Connection", "Store Connection", "access_token"), "shpat_new"
		)
		self.assertEqual(
			get_decrypted_password("Store Connection", "Store Connection", "refresh_token"), "shprt_new"
		)
		queued = [c.args[0] for c in self.enqueue.call_args_list]
		self.assertIn("field_sales.shopify.webhooks.register_webhooks", queued)

	def test_connected_shop_does_not_exchange_again(self):
		self.connect_oauth()
		with patch.object(embedded.requests, "post") as post:
			self.authenticate(make_token())
		post.assert_not_called()

	def test_reauth_required_connection_exchanges_again(self):
		self.connect_oauth(enabled=0, token=None, refresh=None)
		with patch.object(embedded.requests, "post", return_value=_response(200, _token_body())) as post:
			self.authenticate(make_token())
		post.assert_called_once()
		self.assertEqual(self.settings().enabled, 1)

	def test_refused_id_token_asks_for_a_retry(self):
		with patch.object(
			embedded.requests, "post", return_value=_response(400, {"error": "invalid_subject_token"})
		):
			self.assert_retry_401(make_token())
		self.assertFalse(self.settings().enabled)

	def test_another_connected_shop_is_refused(self):
		self.connect_oauth()
		frappe.db.set_single_value("Store Connection", "store_url", "first-shop.myshopify.com")
		with patch.object(embedded.requests, "post") as post:
			self.assert_retry_401(make_token())
		post.assert_not_called()


class TestEmbeddedPage(EmbeddedTestCase):
	def setUp(self):
		super().setUp()
		self.connect_oauth()

	def page(self, query=""):
		self.request(path="/fieldsales", query=query)
		context = frappe._dict()
		fieldsales.get_context(context)
		return context

	def test_embedded_get_has_frame_ancestors_and_no_login(self):
		context = self.page(f"shop={SHOP}&host=YWRtaW4&embedded=1")
		boot = context.boot
		self.assertTrue(boot.embedded)
		self.assertEqual((boot.shop, boot.host, boot.shopify_api_key), (SHOP, "YWRtaW4", APP_ID))
		self.assertNotIn("csrf_token", boot)

		response = Response("ok")
		response.headers["X-Frame-Options"] = "SAMEORIGIN"
		embedded.after_request(response=response, request=frappe.local.request)
		self.assertEqual(
			response.headers["Content-Security-Policy"],
			f"frame-ancestors https://{SHOP} https://admin.shopify.com;",
		)
		self.assertNotIn("X-Frame-Options", response.headers)

	def test_page_renders_app_bridge_first(self):
		self.request(path="/fieldsales", query=f"shop={SHOP}&host=YWRtaW4&embedded=1")
		from frappe.website.serve import get_response

		html = get_response("fieldsales").get_data(as_text=True)
		head = html.split("<head>", 1)[1]
		self.assertTrue(head.lstrip().startswith(f'<meta name="shopify-api-key" content="{APP_ID}"'))
		self.assertLess(head.index("app-bridge.js"), head.index('type="module"'))

	def test_standalone_get_unchanged(self):
		with self.assertRaises(frappe.Redirect):
			self.page()  # Guest: login redirect, as before
		self.assertFalse(getattr(frappe.local, "shopify_frame_shop", None))

		response = Response("ok")
		embedded.after_request(response=response, request=frappe.local.request)
		self.assertNotIn("Content-Security-Policy", response.headers)

		self.request(path="/fieldsales")
		frappe.set_user("Administrator")
		context = frappe._dict()
		fieldsales.get_context(context)
		self.assertIn("csrf_token", context.boot)
		self.assertNotIn("embedded", context.boot)
		html_head = '<meta name="shopify-api-key"'
		from frappe.website.serve import get_response

		self.assertNotIn(html_head, get_response("fieldsales").get_data(as_text=True))

	def test_other_shop_in_query_is_not_embedded(self):
		with self.assertRaises(frappe.Redirect):
			self.page("shop=other-shop.myshopify.com&host=YWRtaW4&embedded=1")

	def test_first_load_after_install_exchanges_id_token(self):
		frappe.db.set_single_value(
			"Store Connection", {"enabled": 0, "store_url": "", "shopify_auth_mode": "Client Credentials"}
		)
		token = make_token()
		with patch.object(embedded.requests, "post", return_value=_response(200, _token_body())):
			context = self.page(f"shop={SHOP}&host=YWRtaW4&embedded=1&id_token={token}")
		self.assertTrue(context.boot.embedded)
		self.assertEqual((self.settings().store_url, self.settings().enabled), (SHOP, 1))

	def test_store_status_for_managers(self):
		frappe.set_user("Administrator")
		status = embedded.get_store_status()
		self.assertEqual(status["shop"], SHOP)
		self.assertIn("locations", status)
