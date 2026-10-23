# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Inactive-retailer anonymisation, plan limits, access log on exports, demo data command."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from field_sales import commands
from field_sales.shopify import billing
from field_sales.utils.anonymise import anonymise_inactive_retailers
from field_sales.utils.limits import ORDERS_PER_MONTH, SALES_REPS, check_limit


class DataProtectionCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"Store Connection",
			{
				"enabled": 0,
				"platform": "Shopify",
				"shopify_auth_mode": "Client Credentials",
				"plan_limits": None,
			},
		)
		self.addCleanup(billing.clear_cache)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def retailer(self, name, status="Active", days_unchanged=0):
		doc = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": name,
				"owner_name": "Owner",
				"phone": "98765 43210",
				"email": "owner@example.com",
				"address_line1": "1 Road",
				"city": "Salem",
				"status": status,
			}
		).insert(ignore_permissions=True)
		if days_unchanged:
			frappe.db.set_value(
				"Retailer",
				doc.name,
				"modified",
				add_days(now_datetime(), -days_unchanged),
				update_modified=False,
			)
		return doc.name


class TestInactiveAnonymisation(DataProtectionCase):
	def test_off_by_default(self):
		old = self.retailer("Old Inactive", "Inactive", 400)
		frappe.db.set_single_value("Store Connection", "retailer_inactive_anonymise_days", 0)
		anonymise_inactive_retailers()
		self.assertEqual(frappe.db.get_value("Retailer", old, "retailer_name"), "Old Inactive")

	def test_anonymises_only_old_inactive_retailers(self):
		old = self.retailer("Old Inactive", "Inactive", 400)
		recent = self.retailer("Recent Inactive", "Inactive", 10)
		active = self.retailer("Old Active", "Active", 400)
		frappe.db.set_single_value("Store Connection", "retailer_inactive_anonymise_days", 365)
		with patch.object(frappe.db, "commit"):
			anonymise_inactive_retailers()
		row = frappe.db.get_value("Retailer", old, ["retailer_name", "email", "owner_name"], as_dict=True)
		self.assertTrue(row.retailer_name.startswith("Redacted"))
		self.assertFalse(row.email or row.owner_name)
		self.assertEqual(frappe.db.get_value("Retailer", recent, "retailer_name"), "Recent Inactive")
		self.assertEqual(frappe.db.get_value("Retailer", active, "retailer_name"), "Old Active")


class TestPlanLimits(DataProtectionCase):
	def oauth(self, limits):
		frappe.db.set_single_value(
			"Store Connection",
			{
				"shopify_auth_mode": "OAuth",
				"store_url": "plan-shop.myshopify.com",
				"plan_limits": frappe.as_json(limits),
			},
		)

	def test_client_credentials_is_never_limited(self):
		frappe.db.set_single_value(
			"Store Connection", "plan_limits", frappe.as_json({"none": {"sales_reps": 0}})
		)
		check_limit(SALES_REPS)

	def test_limit_reached_refuses_with_pricing_link(self):
		self.oauth({"starter": {"sales_reps": 0, "orders_per_month": 100}})
		plan = {"plan": "starter", "trial_ends_at": None, "known": True}
		with (
			patch.object(billing, "get_active_plan", return_value=plan),
			patch.dict(frappe.local.conf, {"shopify_app_handle": "field-sales"}),
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				check_limit(SALES_REPS)
			check_limit(ORDERS_PER_MONTH)  # under its limit
		message = str(ctx.exception)
		self.assertIn("starter", message)
		self.assertIn("https://admin.shopify.com/store/plan-shop/charges/field-sales/pricing_plans", message)

	def test_unknown_plan_or_no_limits_is_not_limited(self):
		self.oauth({"starter": {"sales_reps": 0}})
		with patch.object(
			billing, "get_active_plan", return_value={"plan": None, "trial_ends_at": None, "known": False}
		):
			check_limit(SALES_REPS)
		with patch.object(
			billing, "get_active_plan", return_value={"plan": "pro", "trial_ends_at": None, "known": True}
		):
			check_limit(SALES_REPS)  # "pro" has no limits listed

	def test_partner_api_reads_plan_handle_and_caches(self):
		self.oauth({})
		conf = {
			"shopify_partner_org_id": "1",
			"shopify_partner_api_token": "t",
			"shopify_app_gid": "gid://shopify/App/9",
		}
		body = {"data": {"activeSubscription": {"trialEndsAt": None, "items": [{"handle": "growth"}]}}}

		class Response:
			ok, status_code = True, 200

			def json(self):
				return body

		with (
			patch.dict(frappe.local.conf, conf),
			patch(
				"field_sales.connectors.shopify.ShopifyConnector.get_shop_id",
				return_value="gid://shopify/Shop/5",
			),
			patch.object(billing.requests, "post", return_value=Response()) as post,
		):
			billing.clear_cache()
			self.assertEqual(billing.get_active_plan()["plan"], "growth")
			self.assertEqual(billing.get_active_plan()["plan"], "growth")
		post.assert_called_once()
		self.assertEqual(
			post.call_args.kwargs["json"]["variables"],
			{"appId": "gid://shopify/App/9", "shopId": "gid://shopify/Shop/5"},
		)


class TestAccessLog(DataProtectionCase):
	def test_visit_template_download_is_logged(self):
		from field_sales.visit_import import download_visit_template

		# Frappe writes Access Log rows through a deferred (Redis) insert: check the call.
		with patch("frappe.core.doctype.access_log.access_log.make_access_log") as log:
			download_visit_template()
		self.assertEqual(log.call_args.kwargs["method"], "field_sales.visit_import.download_visit_template")
		self.assertEqual(log.call_args.kwargs["file_type"], "xlsx")


class TestDemoCommand(DataProtectionCase):
	def setUp(self):
		super().setUp()
		frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Demo product",
				"external_product_id": "9900",
				"external_variant_id": "99",
				"status": "Active",
				"price": 25,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)

	def test_refuses_without_passwords_or_on_live_sites(self):
		with patch.dict(frappe.local.conf, {"developer_mode": 1, "field_sales_demo_passwords": None}):
			with self.assertRaises(commands.DemoRefused):
				commands.create_demo_data()
		with (
			patch.dict(frappe.local.conf, {"developer_mode": 0}),
			patch.object(frappe.db, "count", return_value=3),
		):
			with self.assertRaises(commands.DemoRefused):
				commands._check_allowed()

	def test_creates_demo_data_once(self):
		conf = {
			"developer_mode": 1,
			"field_sales_demo_passwords": {"manager": "Demo-Pass-123!", "rep": "Demo-Pass-456!"},
		}
		with patch.dict(frappe.local.conf, conf):
			first = commands.create_demo_data()
			second = commands.create_demo_data()
		self.assertEqual(first, second)
		self.assertEqual(first["retailers"], 10)
		self.assertEqual(first["submitted_orders"], 15)
		self.assertEqual(
			frappe.db.count("Field Order", {"docstatus": 1, "remarks": ("like", "%[demo]%")}), 15
		)
		self.assertGreater(first["visits"], 40)
		rep = frappe.db.get_value(
			"Sales Rep", {"user": commands.REP_EMAIL}, ["reports_to", "commission_plan"], as_dict=True
		)
		self.assertEqual(rep.reports_to, frappe.db.get_value("Sales Rep", {"user": commands.MANAGER_EMAIL}))
		self.assertTrue(rep.commission_plan)
		self.assertIn("Sales Manager", frappe.get_roles(commands.MANAGER_EMAIL))
