# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Two stores on one site: a user of store A reaches nothing of store B, whatever their role,
and vendor staff (a user of no store holding Field Sales Vendor; on a single-store site a System
Manager of no store) reach both.
"""

import datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.tests.store_fixtures import make_store_team, make_user, reset_request_caches
from field_sales.utils.store import (
	VENDOR_ROLE,
	assign_user_to_store,
	clear_store_cache,
	is_vendor,
	set_current_store,
	store_scope,
	user_store,
)

STORE_DOCTYPES = (
	"Retailer",
	"Visit",
	"Field Order",
	"Platform Product",
	"Product Stock",
	"Sales Rep",
	"Commission Plan",
	"Commission Entry",
	"Commission Statement",
	"Payment Transaction",
	"Sync Log",
	"Privacy Request",
)
# DocType -> key of the fixture's record of that doctype.
RECORD = {
	"Retailer": "retailer",
	"Visit": "visit",
	"Field Order": "order",
	"Platform Product": "product",
	"Product Stock": "stock",
	"Sales Rep": "rep",
	"Commission Plan": "plan",
	"Commission Entry": "commission_entry",
	"Commission Statement": "commission_statement",
	"Payment Transaction": "payment_transaction",
	"Sync Log": "sync_log",
	"Privacy Request": "privacy_request",
}
MULTI = {"field_sales_multi_store": 1}


class StoreIsolationCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.a = make_store_team("ISA")
		cls.b = make_store_team("ISB")
		cls.vendor = make_user("isolation.vendor@example.com", ["System Manager", VENDOR_ROLE])
		# A user of store B who is not a rep (yet): store A must not be able to adopt them.
		cls.b.spare = make_user("isb.spare@example.com", ["Field Sales Rep"])
		assign_user_to_store(cls.b.spare, "ISB")

	def setUp(self):
		frappe.set_user("Administrator")
		# A request sets its store for good (e.g. api/webhooks.receive); tests share one process,
		# so an earlier test's store must not become this one's.
		set_current_store(None)
		clear_store_cache()
		reset_request_caches()

	def tearDown(self):
		frappe.set_user("Administrator")
		reset_request_caches()

	def as_user(self, user):
		frappe.set_user(user)
		reset_request_caches()

	def stores_seen(self, doctype):
		return set(frappe.get_list(doctype, pluck="store", limit_page_length=0))


class TestRowIsolation(StoreIsolationCase):
	def test_every_list_stays_in_the_users_store(self):
		for user in (self.a.admin, self.a.manager, self.a.rep_user):
			self.as_user(user)
			for doctype in STORE_DOCTYPES:
				if not frappe.has_permission(doctype, "read"):
					continue  # e.g. Sync Log is for admins only
				seen = self.stores_seen(doctype)
				self.assertNotIn("ISB", seen, f"{user} sees ISB {doctype}")

	def test_lists_are_not_empty_for_the_own_store(self):
		self.as_user(self.a.admin)
		for doctype in (
			"Retailer",
			"Field Order",
			"Visit",
			"Platform Product",
			"Sales Rep",
			"Commission Plan",
		):
			self.assertIn("ISA", self.stores_seen(doctype), doctype)

	def test_other_stores_records_cannot_be_opened_or_changed(self):
		for user in (self.a.admin, self.a.manager):
			self.as_user(user)
			for doctype, key in RECORD.items():
				doc = frappe.get_doc(doctype, self.b[key])
				for ptype in ("read", "write"):
					self.assertFalse(
						frappe.has_permission(doctype, ptype, doc, user=user),
						f"{user} may {ptype} ISB {doctype}",
					)

	def test_own_records_can_be_opened(self):
		self.as_user(self.a.manager)
		for doctype in ("Retailer", "Visit", "Field Order", "Platform Product"):
			doc = frappe.get_doc(doctype, self.a[RECORD[doctype]])
			self.assertTrue(frappe.has_permission(doctype, "read", doc), doctype)

	def test_rep_sees_only_own_team_inside_the_store(self):
		self.as_user(self.a.rep_user)
		self.assertEqual(set(frappe.get_list("Sales Rep", pluck="name")), {self.a.rep})

	def test_vendor_staff_see_every_store(self):
		self.as_user(self.vendor)
		self.assertTrue(is_vendor())
		self.assertEqual(store_scope(), None)
		for doctype in ("Retailer", "Field Order", "Sync Log", "Commission Plan"):
			self.assertTrue({"ISA", "ISB"} <= self.stores_seen(doctype), doctype)

	def test_a_store_admin_is_not_vendor_staff(self):
		self.as_user(self.a.admin)
		self.assertFalse(is_vendor())
		self.assertEqual(store_scope(), "ISA")

	def test_a_store_cannot_be_changed_on_a_saved_record(self):
		self.as_user("Administrator")
		doc = frappe.get_doc("Retailer", self.a.retailer)
		doc.store = "ISB"
		# Refused by the store-change rule, or (first) because its rep stays in store A.
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			doc.save(ignore_permissions=True)

	def test_a_user_cannot_create_a_record_for_another_store(self):
		self.as_user(self.a.manager)
		doc = frappe.get_doc(
			{
				"doctype": "Retailer",
				"store": "ISB",
				"retailer_name": "Sneaky",
				"phone": "9000000000",
				"address_line1": "x",
				"city": "y",
			}
		)
		with self.assertRaises(frappe.PermissionError):
			doc.insert(ignore_permissions=True)

	def test_a_new_record_gets_the_users_store(self):
		self.as_user(self.a.manager)
		doc = frappe.get_doc(
			{"doctype": "Sync Log", "operation": "Product Sync", "status": "Success"}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.store, "ISA")

	def test_a_user_in_no_store_sees_nothing_on_the_shared_site(self):
		loner = make_user("isolation.loner@example.com", ["Sales Manager", "Field Sales Rep"])
		self.as_user(loner)
		with patch.dict(frappe.conf, MULTI):
			self.assertEqual(store_scope(), "")
			for doctype in ("Retailer", "Field Order", "Platform Product"):
				self.assertEqual(frappe.get_list(doctype, pluck="name"), [], doctype)


class TestStoreAccess(StoreIsolationCase):
	def test_store_admin_reaches_only_their_own_store(self):
		self.as_user(self.a.admin)
		self.assertTrue(frappe.has_permission("Store", "read", "ISA"))
		self.assertTrue(frappe.has_permission("Store", "write", "ISA"))
		self.assertFalse(frappe.has_permission("Store", "read", "ISB"))
		self.assertEqual(frappe.get_list("Store", pluck="name"), ["ISA"])

	def test_store_admin_cannot_create_or_delete_stores(self):
		self.as_user(self.a.admin)
		self.assertFalse(frappe.has_permission("Store", "create"))
		self.assertFalse(frappe.has_permission("Store", "delete", "ISA"))

	def test_vendor_only_settings_are_closed_to_merchants(self):
		self.as_user(self.a.admin)
		self.assertFalse(frappe.has_permission("Store Connection", "read"))
		self.assertFalse(frappe.has_permission("Mobile Push Settings", "read"))

	def test_user_store_and_assignment(self):
		self.assertEqual(user_store(self.a.manager), "ISA")
		self.assertIsNone(user_store(self.vendor))
		# Safe to repeat; a second, different store is refused.
		assign_user_to_store(self.a.manager, "ISA")
		with self.assertRaises(frappe.ValidationError):
			assign_user_to_store(self.a.manager, "ISB")

	def test_each_users_store_comes_from_their_rep_or_retailer_record(self):
		self.assertEqual(user_store(self.b.rep_user), "ISB")
		self.assertEqual(user_store(self.b.retailer_user), "ISB")


class TestEndpointIsolation(StoreIsolationCase):
	def test_mobile_lists_hold_only_the_users_store(self):
		from field_sales.api import mobile

		self.as_user(self.a.manager)
		for fn, key in (
			(mobile.get_orders_list, "ISB-order"),
			(mobile.get_visits_list, "ISB-visit"),
			(mobile.get_customers_list, "ISB-customer"),
			(mobile.get_products_list, "ISB-product"),
		):
			result = fn()
			names = [row.get("name") for row in result["items"]]
			self.assertTrue(names, fn.__name__)
			for name in names:
				self.assertNotIn(name, (self.b.order, self.b.visit, self.b.retailer, self.b.product), key)

	def test_mobile_search_finds_only_the_users_store(self):
		from field_sales.api.mobile import mobile_search

		self.as_user(self.a.manager)
		text = str(mobile_search(query="ISB"))
		self.assertNotIn(self.b.retailer, text)
		self.assertNotIn(self.b.product, text)

	def test_dashboard_and_summary_do_not_count_the_other_store(self):
		from field_sales.api.dashboard import get_dashboard

		self.as_user(self.a.admin)
		names = [row.get("name") for row in get_dashboard("month")["recent_orders"]]
		self.assertIn(self.a.order, names)
		self.assertNotIn(self.b.order, names)

	def test_endpoints_taking_a_record_name_refuse_the_other_store(self):
		from field_sales.api import get_retailer_summary, get_visit_orders, mobile

		self.as_user(self.a.manager)
		with self.assertRaises(frappe.PermissionError):
			get_retailer_summary(self.b.retailer)
		with self.assertRaises(frappe.PermissionError):
			get_visit_orders(self.b.visit)
		with self.assertRaises(frappe.PermissionError):
			mobile.get_order_detail(self.b.order)

	def test_store_admin_cannot_reset_or_adopt_the_other_stores_users(self):
		from field_sales.api import create_sales_rep, get_user_for_sales_rep, set_sales_rep_password

		self.as_user(self.a.admin)
		with self.assertRaises(frappe.PermissionError):
			set_sales_rep_password(self.b.rep, "Str0ng!Passw0rd#1")
		self.assertFalse(get_user_for_sales_rep(self.b.manager)["exists"])
		with self.assertRaises(frappe.PermissionError):
			create_sales_rep(user=self.b.spare, full_name="Hijack")

	def test_rep_lookup_hides_accounts_of_no_store_the_admin_cannot_adopt(self):
		from field_sales.api import get_user_for_sales_rep

		unclaimed = make_user("isolation.unclaimed@example.com", ["Field Sales Rep"])
		self.as_user(self.a.admin)
		with patch.dict(frappe.conf, MULTI):
			# Vendor staff hold site-wide roles: a store must not learn they have an account,
			# nor see the refusal that hid it.
			frappe.clear_messages()
			self.assertFalse(get_user_for_sales_rep(self.vendor)["exists"])
			self.assertFalse(frappe.get_message_log())
			# A plain account of no store may still be adopted, so it is shown.
			self.assertTrue(get_user_for_sales_rep(unclaimed)["exists"])

	def test_mobile_rep_filter_outside_the_team_matches_nothing(self):
		from field_sales.api import mobile

		self.as_user(self.a.rep_user)
		self.assertTrue(mobile.get_visits_list(sales_rep=self.a.rep)["items"])
		# Not narrowed to the manager's visits by dropping the filter: none at all.
		self.assertEqual(mobile.get_visits_list(sales_rep=self.a.manager_rep)["items"], [])

	def test_commission_plans_with_the_same_name_live_in_both_stores(self):
		self.assertEqual(frappe.db.get_value("Commission Plan", self.a.plan, "plan_name"), "Standard")
		self.assertEqual(frappe.db.get_value("Commission Plan", self.b.plan, "plan_name"), "Standard")
		self.assertNotEqual(self.a.plan, self.b.plan)
		self.as_user(self.a.admin)
		self.assertEqual(frappe.get_list("Commission Plan", pluck="name"), [self.a.plan])

	def test_plan_usage_counts_only_the_own_store(self):
		from field_sales.shopify.billing import usage
		from field_sales.utils.store import use_store

		with use_store("ISA"):
			reps_a = usage("sales_reps")
		self.assertEqual(reps_a, frappe.db.count("Sales Rep", {"store": "ISA", "active": 1}))

	def test_alert_recipients_are_the_stores_own_users(self):
		from field_sales.sync.notify import users_with_roles

		managers = users_with_roles(("Sales Manager",), "ISA")
		self.assertIn(self.a.manager, managers)
		self.assertNotIn(self.b.manager, managers)
		admins = users_with_roles(("Store Admin",), "ISB")
		self.assertIn(self.b.admin, admins)
		self.assertNotIn(self.a.admin, admins)

	def test_vendor_recipients_exclude_store_users(self):
		from field_sales.sync.notify import users_with_roles

		vendors = users_with_roles(("System Manager",), vendor=True)
		self.assertIn(self.vendor, vendors)
		self.assertNotIn(self.a.admin, vendors)


class TestWritePathIsolation(StoreIsolationCase):
	def test_a_store_admin_cannot_add_commission_to_the_other_stores_rep(self):
		from field_sales.commission.api import create_adjustment

		self.as_user(self.a.admin)
		with (
			patch("field_sales.commission.require_commission_enabled"),
			self.assertRaises(frappe.PermissionError),
		):
			create_adjustment(self.b.rep, 100, "Hijack")

	def test_a_store_admin_cannot_approve_the_other_stores_statement(self):
		from field_sales.commission.api import approve_statement

		self.as_user(self.a.admin)
		with self.assertRaises(frappe.PermissionError):
			approve_statement(self.b.commission_statement)

	def test_a_store_admin_cannot_check_out_the_other_stores_visit(self):
		from field_sales.api import check_out

		self.as_user(self.a.admin)
		with self.assertRaises(frappe.PermissionError):
			check_out(self.b.visit, 11.0, 77.0)

	def test_a_new_record_for_the_other_stores_rep_is_refused(self):
		self.as_user(self.a.manager)
		visit = frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.b.rep,
				"retailer": self.b.retailer,
				"visit_date": frappe.utils.nowdate(),
				"status": "Planned",
			}
		)
		with self.assertRaises(frappe.PermissionError):
			visit.insert()

	def test_a_record_of_a_rep_is_stamped_with_the_reps_store(self):
		from field_sales.utils.store import use_store

		self.as_user("Administrator")
		with use_store("ISA"):  # a stale context must not decide the store
			log = frappe.get_doc(
				{
					"doctype": "Visit",
					"sales_rep": self.b.rep,
					"retailer": self.b.retailer,
					"visit_date": frappe.utils.add_days(frappe.utils.nowdate(), 3),
					"status": "Planned",
				}
			).insert(ignore_permissions=True)
		self.assertEqual(log.store, "ISB")


class TestStoreAdminLimits(StoreIsolationCase):
	"""Store Admin is an admin inside its store, never vendor staff (review of PR #43)."""

	# These tests change Store ISA: undo each one (the class itself rolls back only once).
	def setUp(self):
		super().setUp()
		frappe.db.savepoint("store_admin_limits")

	def tearDown(self):
		frappe.db.rollback(save_point="store_admin_limits")
		super().tearDown()

	def test_a_store_admin_cannot_change_the_embedded_user_or_credentials(self):
		self.as_user(self.a.admin)
		for field, value in (
			("embedded_user", "Administrator"),
			("embedded_user", self.vendor),
			("shopify_client_id", "someone-elses-app"),
			("shopify_api_version", "2020-01"),
		):
			doc = frappe.get_doc("Store", "ISA")
			doc.set(field, value)
			with self.subTest(field=field, value=value), self.assertRaises(frappe.PermissionError):
				doc.save()

	def test_a_store_admin_can_still_edit_their_store_settings(self):
		self.as_user(self.a.admin)
		doc = frappe.get_doc("Store", "ISA")
		doc.company_name = "ISA Traders"
		doc.save()
		self.assertEqual(frappe.db.get_value("Store", "ISA", "company_name"), "ISA Traders")

	def test_the_embedded_user_is_never_administrator_or_another_stores_user(self):
		for user in ("Administrator", self.b.manager, self.vendor):
			doc = frappe.get_doc("Store", "ISA")
			doc.embedded_user = user
			with self.subTest(user=user), self.assertRaises(frappe.ValidationError):
				doc.save()

	def test_a_shopify_token_never_signs_in_as_administrator(self):
		from field_sales.shopify.embedded import get_embedded_user
		from field_sales.utils.store import use_store

		frappe.db.set_value("Store", "ISA", "embedded_user", "Administrator")
		# It commits once it has made the store's own embedded user; not in a test.
		with use_store("ISA"), patch("field_sales.shopify.embedded.safe_commit"):
			self.assertNotEqual(get_embedded_user(), "Administrator")

	def test_a_shopify_token_never_signs_in_as_vendor_staff_or_another_stores_user(self):
		from field_sales.shopify.embedded import get_embedded_user
		from field_sales.utils.store import use_store

		for user in (self.vendor, self.b.manager):
			# Written without validation, as an older value or a direct write would be.
			frappe.db.set_value("Store", "ISA", "embedded_user", user)
			with self.subTest(user=user), use_store("ISA"), patch("field_sales.shopify.embedded.safe_commit"):
				self.assertNotEqual(get_embedded_user(), user)

	def test_a_single_store_sites_system_manager_still_changes_the_connection(self):
		from field_sales.tests.store_fixtures import make_user

		# The site owner: System Manager and Sales Manager, so assigned to the store.
		owner = make_user("isa.owner@example.com", ["System Manager", "Sales Manager"])
		assign_user_to_store(owner, "ISA")
		self.as_user(owner)
		doc = frappe.get_doc("Store", "ISA")
		doc.shopify_api_version = "2026-10"
		doc.save()
		self.assertEqual(frappe.db.get_value("Store", "ISA", "shopify_api_version"), "2026-10")

		# On the shared site the same user is a merchant's, not the vendor's.
		with patch.dict(frappe.conf, MULTI):
			doc = frappe.get_doc("Store", "ISA")
			doc.shopify_api_version = "2026-07"
			with self.assertRaises(frappe.PermissionError):
				doc.save()

	def test_rep_performance_shows_only_the_own_stores_reps(self):
		from field_sales.field_sales.report.rep_performance.rep_performance import get_reps_in_scope

		self.as_user(self.a.admin)
		reps = get_reps_in_scope({})
		self.assertIn(self.a.rep, reps)
		self.assertEqual(
			{
				r.store
				for r in frappe.get_all("Sales Rep", filters={"name": ("in", list(reps))}, fields=["store"])
			},
			{"ISA"},
		)
		self.assertEqual(get_reps_in_scope({"sales_rep": self.b.rep}), {})

	def test_commission_summary_shows_only_the_own_stores_reps(self):
		from field_sales.field_sales.report.rep_commission_summary.rep_commission_summary import (
			_reps_in_scope,
		)

		self.as_user(self.a.admin)
		reps = _reps_in_scope(frappe._dict())
		self.assertIn(self.a.rep, reps)
		self.assertNotIn(self.b.rep, reps)
		self.assertEqual(_reps_in_scope(frappe._dict(sales_rep=self.b.rep)), [])
		self.assertEqual(_reps_in_scope(frappe._dict(manager=self.b.manager_rep)), [])

	def test_a_store_admin_cannot_move_a_rep_to_another_user(self):
		self.as_user(self.a.admin)
		rep = frappe.get_doc("Sales Rep", self.a.rep)
		rep.user = self.vendor
		with self.assertRaises(frappe.PermissionError):
			rep.save()
		self.assertIsNone(user_store(self.vendor))


class TestEndpointAudit(IntegrationTestCase):
	"""Every whitelisted function is in docs/multi-merchant/inventory.md section 8, with how it stays in the store."""

	def test_every_whitelisted_function_is_reviewed(self):
		import ast
		import re
		from pathlib import Path

		app = Path(frappe.get_app_path("field_sales"))
		found = set()
		for path in app.rglob("*.py"):
			rel = path.relative_to(app).with_suffix("")
			if "tests" in rel.parts or "patches" in rel.parts:
				continue
			try:
				tree = ast.parse(path.read_text())
			except SyntaxError:
				continue
			module = ".".join(rel.parts).removesuffix(".__init__")
			for node in ast.walk(tree):
				if isinstance(node, ast.FunctionDef) and any(
					"frappe.whitelist" in ast.unparse(d) for d in node.decorator_list
				):
					found.add(f"field_sales.{module}.{node.name}")

		doc = app.parent / "docs" / "multi-merchant" / "inventory.md"
		if not doc.exists():
			self.skipTest("docs are not shipped with this install")
		listed = set(re.findall(r"^\| `(field_sales\.[\w.]+)` \|", doc.read_text(), re.M))
		self.assertEqual(
			sorted(found - listed),
			[],
			"whitelisted but not in inventory.md section 8: review store isolation",
		)
		self.assertEqual(
			sorted(listed - found), [], "listed in inventory.md section 8 but no longer whitelisted"
		)


class TestStoreScopedJobs(StoreIsolationCase):
	"""Jobs and handlers of one store leave every other store's data alone (review 2 of PR #43)."""

	def setUp(self):
		super().setUp()
		frappe.db.savepoint("store_scoped_jobs")

	def tearDown(self):
		frappe.db.rollback(save_point="store_scoped_jobs")
		super().tearDown()

	def test_shop_redact_touches_only_the_requests_store(self):
		from frappe.utils.password import get_decrypted_password, set_encrypted_password

		from field_sales.shopify.privacy import shop_redact

		set_encrypted_password("Store", "ISB", "b-token", "access_token")
		b_retailer = frappe.db.get_value("Retailer", self.b.retailer, "retailer_name")
		frappe.db.set_value("Sync Log", self.b.sync_log, "request_payload", '{"b": 1}')

		shop_redact(frappe._dict(name="PRIV-TEST", store="ISA"), {})

		self.assertFalse(frappe.db.exists("Platform Product", self.a.product))
		self.assertTrue(frappe.db.exists("Platform Product", self.b.product))
		self.assertTrue(frappe.db.exists("Product Stock", self.b.stock))
		self.assertEqual(frappe.db.get_value("Retailer", self.b.retailer, "retailer_name"), b_retailer)
		self.assertEqual(frappe.db.get_value("Sync Log", self.b.sync_log, "request_payload"), '{"b": 1}')
		self.assertEqual(
			get_decrypted_password("Store", "ISB", "access_token", raise_exception=False), "b-token"
		)

	def test_a_product_sync_archives_only_its_own_stores_products(self):
		from field_sales.sync.products import _archive_missing

		_archive_missing("Shopify", set(), "ISA")
		self.assertEqual(frappe.db.get_value("Platform Product", self.a.product, "status"), "Archived")
		self.assertEqual(frappe.db.get_value("Platform Product", self.b.product, "status"), "Active")

	def test_product_sync_jobs_are_queued_per_store(self):
		from field_sales.sync.products import sync_job_id

		self.assertNotEqual(sync_job_id("ISA"), sync_job_id("ISB"))

	def test_each_store_has_its_own_cached_plan(self):
		from field_sales.shopify import billing
		from field_sales.utils.store import use_store

		plans = {"ISA": "starter", "ISB": "pro"}

		def fetch(store=None):
			return {"plan": plans[store], "trial_ends_at": None, "known": True}

		for store in plans:
			billing.clear_cache(store)
			self.addCleanup(billing.clear_cache, store)
		with patch.object(billing, "_fetch_plan", side_effect=fetch):
			self.assertEqual(billing.get_active_plan("ISA")["plan"], "starter")
			with use_store("ISB"):
				self.assertEqual(billing.get_active_plan()["plan"], "pro")

	def test_a_rep_hierarchy_never_reaches_into_another_store(self):
		from field_sales.permissions import get_allowed_reps

		# A rep of store B wrongly reporting to store A's manager is still not store A's.
		frappe.db.set_value("Sales Rep", self.b.rep, "reports_to", self.a.manager_rep)
		self.assertNotIn(self.b.rep, get_allowed_reps(self.a.manager))
		self.assertIn(self.a.rep, get_allowed_reps(self.a.manager))

	def test_the_shared_site_keeps_its_own_currency_and_time_zone(self):
		from field_sales.sync.shop_settings import sync_shop_settings
		from field_sales.utils.store import use_store

		currency = frappe.db.get_default("currency")
		time_zone = frappe.db.get_single_value("System Settings", "time_zone")
		frappe.db.set_value("Store", "ISA", "match_shop_settings", 1)
		other_tz = "America/New_York" if time_zone != "America/New_York" else "Asia/Kolkata"
		with patch.dict(frappe.conf, MULTI), use_store("ISA"):
			result = sync_shop_settings({"currency": "USD", "timezone": other_tz, "country": "US"})
		self.assertEqual(result["changes"], [])
		self.assertEqual(frappe.db.get_default("currency"), currency)
		self.assertEqual(frappe.db.get_single_value("System Settings", "time_zone"), time_zone)
		self.assertEqual(frappe.db.get_value("Store", "ISA", "shop_timezone"), other_tz)


# Filled by _record_current_store, a job run through run_in_store.
JOB_STORES: list = []


def _record_current_store():
	from field_sales.utils.store import current_store

	JOB_STORES.append(current_store())


class TestStoreContext(StoreIsolationCase):
	"""Phase 4: requests and jobs without a store user still work for the right store."""

	SHOP_A = "isa-isolation.myshopify.com"
	SHOP_B = "isb-isolation.myshopify.com"

	def setUp(self):
		super().setUp()
		frappe.db.savepoint("store_context")
		JOB_STORES.clear()

	def tearDown(self):
		frappe.db.rollback(save_point="store_context")
		super().tearDown()

	# -- jobs ------------------------------------------------------------------

	def test_a_queued_job_runs_in_the_store_it_was_queued_for(self):
		from field_sales.utils.store import enqueue_in_store, run_in_store

		self.as_user("Guest")
		with patch("frappe.enqueue") as enqueue:
			enqueue_in_store(f"{__name__}._record_current_store", store="ISB", queue="short")
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(enqueue.call_args.args, ("field_sales.utils.store.run_in_store",))
		self.assertEqual(kwargs["store_name"], "ISB")
		self.assertEqual(kwargs["queue"], "short")

		run_in_store(kwargs["store_method"], kwargs["store_name"])
		self.assertEqual(JOB_STORES, ["ISB"])

	def test_administrator_must_name_the_store_when_there_are_several(self):
		from field_sales.utils.store import NoStoreContext, enqueue_in_store

		with patch("frappe.enqueue") as enqueue, self.assertRaises(NoStoreContext):
			enqueue_in_store(f"{__name__}._record_current_store")
		enqueue.assert_not_called()

	def test_a_record_job_goes_to_the_records_store(self):
		from field_sales.sync.orders import enqueue_push_order

		with patch("frappe.enqueue") as enqueue:
			enqueue_push_order(self.b.order)
		self.assertEqual(enqueue.call_args.kwargs["store_name"], "ISB")

	def test_each_store_runs_even_when_another_fails(self):
		from field_sales.utils.store import current_store, for_each_store

		def job():
			JOB_STORES.append(current_store())
			frappe.db.set_value("Store", current_store(), "last_test_status", "touched by job")
			if current_store() == "ISA":
				raise ValueError("store A is broken")

		with (
			patch.object(frappe, "log_error") as log_error,
			patch("field_sales.utils.store.safe_commit") as commit,
			self.assertRaisesRegex(frappe.ValidationError, "test_job failed for ISA"),
		):
			for_each_store(job, title="test_job", enabled_only=False)
		commit.assert_called_once()  # the other stores' work is kept before the job fails
		self.assertIn("ISA", JOB_STORES)
		self.assertIn("ISB", JOB_STORES)
		# Store A's write is undone, store B's is kept.
		self.assertNotEqual(frappe.db.get_value("Store", "ISA", "last_test_status"), "touched by job")
		self.assertEqual(frappe.db.get_value("Store", "ISB", "last_test_status"), "touched by job")
		self.assertEqual(log_error.call_count, 1)
		self.assertEqual(log_error.call_args.kwargs["title"], "Field Sales scheduled job failed: test_job (ISA)")

	def test_order_retries_stay_inside_each_store(self):
		from field_sales.sync import orders
		from field_sales.utils.store import current_store

		for team in (self.a, self.b):
			frappe.db.set_value("Store", team.store, "enabled", 1)
			frappe.db.set_value(
				"Field Order", team.order, {"docstatus": 1, "sync_status": "Failed", "sync_attempts": 0}
			)
		queued = []
		with patch.object(
			orders, "enqueue_push_order", side_effect=lambda n, store=None: queued.append((n, current_store()))
		):
			orders.retry_failed_orders()
		self.assertIn((self.a.order, "ISA"), queued)
		self.assertIn((self.b.order, "ISB"), queued)
		self.assertNotIn((self.b.order, "ISA"), queued)

	# -- webhooks, sign-in, install ----------------------------------------------

	def test_a_shop_maps_to_its_own_store(self):
		from field_sales.utils.store import shop_store

		self.assertEqual(shop_store(self.SHOP_B), "ISB")
		self.assertEqual(shop_store(self.SHOP_B.upper()), "ISB")
		# Several stores and none is this shop's: no store, not an error.
		self.assertIsNone(shop_store("unknown-isolation.myshopify.com"))
		with patch.dict(frappe.conf, MULTI):
			self.assertIsNone(shop_store("unknown-isolation.myshopify.com"))

	def test_session_token_checks_use_the_shops_store(self):
		from field_sales.shopify import session_token

		self.assertEqual(session_token._connection(self.SHOP_B).name, "ISB")
		with patch.dict(frappe.conf, MULTI):
			self.assertIsNone(session_token._connection("unknown-isolation.myshopify.com"))

	def test_uninstall_disables_only_that_shops_store(self):
		from field_sales.shopify.webhooks import handle_uninstalled
		from field_sales.utils.store import use_store

		for store in ("ISA", "ISB"):
			frappe.db.set_value("Store", store, "enabled", 1)
		with use_store("ISB"):
			handle_uninstalled(self.SHOP_B)
		self.assertEqual(frappe.db.get_value("Store", "ISB", "enabled"), 0)
		self.assertEqual(frappe.db.get_value("Store", "ISA", "enabled"), 1)

	def test_a_new_shop_installs_into_a_store_of_its_own(self):
		from field_sales.shopify import oauth
		from field_sales.utils.store import current_store, store_for_shop

		shop = "isnew-isolation.myshopify.com"
		self.addCleanup(oauth.set_current_store, None)
		with (
			patch.dict(frappe.conf, MULTI),
			patch.object(oauth, "save_oauth_tokens"),
			patch.object(oauth, "enqueue_in_store") as enqueue,
		):
			oauth.check_shop_allowed(shop)
			oauth.save_installation(shop, {"scope": ""})
		store = store_for_shop(shop)
		self.assertTrue(store)
		self.assertNotIn(store, ("ISA", "ISB"))
		self.assertEqual(current_store(), store)
		self.assertEqual({c.kwargs["store"] for c in enqueue.call_args_list}, {store})
		self.assertEqual(frappe.db.get_value("Store", "ISA", "store_url"), self.SHOP_A)

	def test_a_shop_cannot_install_over_another_shops_store_on_a_single_store_site(self):
		from field_sales.shopify import oauth
		from field_sales.utils.store import use_store

		# One store: an install for another shop is refused ("one site, one shop").
		with use_store("ISA"), patch.object(oauth, "shop_store", return_value="ISA"):
			self.assertRaises(oauth.InstallError, oauth.check_shop_allowed, self.SHOP_B)

	def test_razorpay_webhook_is_not_served_on_the_shared_site(self):
		from field_sales.payments import razorpay

		with patch.dict(frappe.conf, MULTI):
			result = razorpay.webhook()
		self.assertEqual(result, {"status": "not served on this site"})
		self.assertEqual(frappe.local.response.http_status_code, 404)

	def test_uninstalled_stores_get_no_daily_summary(self):
		from field_sales import daily_summary
		from field_sales.utils.store import current_store

		frappe.db.set_value("Store", "ISB", "uninstalled_on", frappe.utils.now_datetime())
		seen = []
		evening = datetime.datetime(2026, 12, 2, 20, 0)
		for store in ("ISA", "ISB"):
			key = frappe.cache.make_key(f"field_sales:daily_summary:{store}:2026-12-02")
			frappe.cache.delete(key)
			self.addCleanup(frappe.cache.delete, key)
		with (
			patch.object(daily_summary, "store_now", return_value=evening),
			patch.object(daily_summary, "_get_managers", side_effect=lambda: seen.append(current_store()) or []),
		):
			daily_summary.send_daily_summary()
		self.assertIn("ISA", seen)
		self.assertNotIn("ISB", seen)

	# -- requests routed by shop -----------------------------------------------------

	def fake_request(self, body: bytes, headers: dict):
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		builder = EnvironBuilder(method="POST", data=body, headers=headers, content_type="application/json")
		frappe.local.request = Request(builder.get_environ())
		frappe.local.response = frappe._dict()
		self.addCleanup(setattr, frappe.local, "request", None)

	def give_secrets(self):
		"""Store A and B each sign with their own client credentials secret."""
		from frappe.utils.password import set_encrypted_password

		for store in ("ISA", "ISB"):
			frappe.db.set_value("Store", store, {"enabled": 1, "shopify_client_id": f"{store}-id"})
			set_encrypted_password("Store", store, f"{store}-secret", "shopify_client_secret")

	@staticmethod
	def sign(body: bytes, secret: str) -> str:
		import base64
		import hashlib
		import hmac

		return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

	def test_a_store_webhook_is_verified_and_queued_for_the_shops_store(self):
		from field_sales.api import webhooks
		from field_sales.utils.store import set_current_store

		self.give_secrets()
		self.addCleanup(set_current_store, None)
		body = b'{"inventory_item_id": 1, "location_id": 2, "available": 3}'
		headers = {
			"X-Shopify-Shop-Domain": self.SHOP_B,
			"X-Shopify-Topic": "inventory_levels/update",
			"X-Shopify-Webhook-Id": frappe.generate_hash(length=12),
		}

		self.fake_request(body, {**headers, "X-Shopify-Hmac-Sha256": self.sign(body, "ISB-secret")})
		with patch("frappe.enqueue") as enqueue:
			webhooks.receive()
		self.assertEqual(frappe.local.response.http_status_code, 200)
		self.assertEqual(enqueue.call_args.kwargs["store_name"], "ISB")

		# Store A's secret cannot speak for store B's shop.
		headers["X-Shopify-Webhook-Id"] = frappe.generate_hash(length=12)
		self.fake_request(body, {**headers, "X-Shopify-Hmac-Sha256": self.sign(body, "ISA-secret")})
		with patch("frappe.enqueue") as enqueue, patch.object(webhooks, "_log_invalid_signature"):
			webhooks.receive()
		self.assertEqual(frappe.local.response.http_status_code, 401)
		enqueue.assert_not_called()

	def test_a_privacy_webhook_is_recorded_in_the_shops_store(self):
		from field_sales.shopify import webhooks
		from field_sales.utils.store import set_current_store

		self.give_secrets()
		self.addCleanup(set_current_store, None)
		body = frappe.as_json({"customer": {"id": 42}}).encode()
		self.fake_request(
			body,
			{
				"X-Shopify-Shop-Domain": self.SHOP_B,
				"X-Shopify-Topic": "customers/redact",
				"X-Shopify-Webhook-Id": frappe.generate_hash(length=12),
				"X-Shopify-Hmac-Sha256": self.sign(body, "ISB-secret"),
			},
		)
		with patch("frappe.enqueue") as enqueue:
			webhooks.receive()
		self.assertEqual(frappe.local.response.http_status_code, 200)
		request = enqueue.call_args.kwargs["privacy_request"]
		self.assertEqual(frappe.db.get_value("Privacy Request", request, "store"), "ISB")
		self.assertEqual(enqueue.call_args.kwargs["store_name"], "ISB")

	def test_a_session_token_signs_in_to_the_shops_store(self):
		from field_sales.shopify import embedded, session_token
		from field_sales.utils.store import current_store, set_current_store

		self.addCleanup(set_current_store, None)
		seen = []
		self.as_user("Guest")
		with (
			patch.object(session_token, "bearer_token", return_value="token"),
			patch.object(session_token, "_looks_like_session_token", return_value=True),
			patch.object(session_token, "decode", return_value={"shop": self.SHOP_B, "sub": "1"}),
			patch.object(embedded, "is_connected", return_value=True),
			patch.object(
				embedded, "get_embedded_user", side_effect=lambda: seen.append(current_store()) or self.b.admin
			),
		):
			session_token.authenticate()
		self.assertEqual(seen, ["ISB"])
		self.assertEqual(frappe.session.user, self.b.admin)
		self.assertEqual(current_store(), "ISB")


class TestStoreLocale(StoreIsolationCase):
	"""Phase 5: each store's own clock decides "today" and "this month" on the shared site."""

	# The site clock (Asia/Kolkata): 08:00 on the 1st is still the 30th in New York.
	SITE_NOW = datetime.datetime(2026, 12, 1, 8, 0)

	def setUp(self):
		super().setUp()
		from field_sales.utils.store import clear_timezone_cache

		frappe.db.savepoint("store_locale")
		self.addCleanup(clear_timezone_cache)
		clear_timezone_cache()
		frappe.db.set_value("Store", "ISA", "shop_timezone", "Asia/Kolkata")
		frappe.db.set_value("Store", "ISB", "shop_timezone", "America/New_York")
		self.multi = patch.dict(frappe.conf, MULTI)
		self.multi.start()
		self.addCleanup(self.multi.stop)
		self.clock = patch("field_sales.utils.store.now_datetime", return_value=self.SITE_NOW)
		self.clock.start()
		self.addCleanup(self.clock.stop)
		self.site_zone = patch("field_sales.utils.store.get_system_timezone", return_value="Asia/Kolkata")
		self.site_zone.start()
		self.addCleanup(self.site_zone.stop)

	def tearDown(self):
		frappe.db.rollback(save_point="store_locale")
		super().tearDown()

	def test_each_store_has_its_own_today(self):
		from field_sales.utils.store import store_month_bounds, store_now, store_today

		self.assertEqual(store_today("ISA"), datetime.date(2026, 12, 1))
		self.assertEqual(store_today("ISB"), datetime.date(2026, 11, 30))
		self.assertEqual(store_now("ISB"), datetime.datetime(2026, 11, 30, 21, 30))
		self.assertEqual(store_month_bounds(store="ISB"), (datetime.date(2026, 11, 1), datetime.date(2026, 11, 30)))

	def test_a_store_midnight_in_site_time(self):
		from field_sales.utils.store import site_to_store, store_to_site

		midnight_ny = datetime.datetime(2026, 12, 1, 0, 0)
		on_site = store_to_site(midnight_ny, "ISB")
		self.assertEqual(on_site, datetime.datetime(2026, 12, 1, 10, 30))
		self.assertEqual(site_to_store(on_site, "ISB"), midnight_ny)

	def test_an_empty_store_date_is_the_stores_today(self):
		from field_sales.utils.store import fill_store_dates

		for doctype, field in (("Visit", "visit_date"), ("Field Order", "order_date")):
			doc = frappe.get_doc({"doctype": doctype, "store": "ISB"})
			fill_store_dates(doc)
			self.assertEqual(doc.get(field), datetime.date(2026, 11, 30))
		# A date the user chose stays.
		doc = frappe.get_doc({"doctype": "Visit", "store": "ISB", "visit_date": "2026-11-28"})
		fill_store_dates(doc)
		self.assertEqual(str(doc.visit_date), "2026-11-28")

	def test_the_dashboard_today_follows_the_users_store(self):
		from field_sales.frontend_api import get_card_presets

		self.as_user(self.b.manager)
		presets = get_card_presets()
		self.assertIn(["order_date", "=", "2026-11-30"], presets["orders_today"]["filters"])

	def test_an_unknown_zone_or_a_single_store_site_uses_the_site_clock(self):
		from field_sales.utils.store import clear_timezone_cache, store_timezone

		frappe.db.set_value("Store", "ISB", "shop_timezone", "Mars/Olympus")
		clear_timezone_cache()
		self.assertEqual(store_timezone("ISB"), "Asia/Kolkata")
		self.multi.stop()
		clear_timezone_cache()
		frappe.db.set_value("Store", "ISB", "shop_timezone", "America/New_York")
		self.assertEqual(store_timezone("ISB"), "Asia/Kolkata")
		self.multi.start()

	# -- Step 2: clocks of the web app, Desk and local-time jobs ------------------------

	def test_the_session_tells_the_app_both_zones(self):
		from field_sales.frontend_api import get_session

		self.as_user(self.b.manager)
		session = get_session()
		self.assertEqual(session["time_zone"], "America/New_York")
		self.assertEqual(session["server_time_zone"], frappe.utils.get_system_timezone())

	def test_a_new_store_user_sees_desk_in_the_stores_zone(self):
		user = make_user("isb.zone@example.com", ["Field Sales Rep"])
		assign_user_to_store(user, "ISB")
		self.assertEqual(frappe.db.get_value("User", user, "time_zone"), "America/New_York")

	def run_summaries(self, site_now, runs=1, fail_first=False) -> list:
		"""Run the hourly summary ``runs`` times at ``site_now``; returns (store, manager) per sent mail."""
		from field_sales import daily_summary
		from field_sales.utils.store import current_store

		self.clock.stop()
		self.clock = patch("field_sales.utils.store.now_datetime", return_value=site_now)
		self.clock.start()
		manager = frappe._dict(user="isb.summary@example.com", name="ISB-MGR", full_name="ISB Manager")
		for store, day in (("ISA", site_now.date()), ("ISB", (site_now - datetime.timedelta(days=1)).date())):
			for date in (site_now.date(), day):
				for key in daily_summary._summary_keys(store, str(date), manager.user):
					frappe.cache.delete(key)
					self.addCleanup(frappe.cache.delete, key)
		sent, failures = [], [fail_first]

		def send(mgr, today):
			if failures[0]:
				failures[0] = False
				raise ConnectionError("mail queue down")
			sent.append((current_store(), mgr.user))

		with (
			patch.object(daily_summary, "_get_managers", side_effect=lambda: [manager] if current_store() in ("ISA", "ISB") else []),
			patch.object(daily_summary, "_send_to_manager", side_effect=send),
			patch.object(frappe, "log_error"),
		):
			for _ in range(runs):
				daily_summary.send_daily_summary()
		return sent

	def test_the_daily_summary_goes_at_each_stores_evening_once(self):
		# 06:30 on the site (Kolkata) is 20:00 of the day before in New York, morning in store A.
		sent = self.run_summaries(datetime.datetime(2026, 12, 1, 6, 30), runs=2)
		self.assertEqual(sent, [("ISB", "isb.summary@example.com")])

	def test_a_late_run_still_sends_the_days_summary_once(self):
		# 07:35 on the site is 21:05 in New York: the 20:00 run was missed.
		sent = self.run_summaries(datetime.datetime(2026, 12, 1, 7, 35), runs=2)
		self.assertEqual(sent, [("ISB", "isb.summary@example.com")])

	def test_a_failed_summary_is_tried_again_next_run(self):
		sent = self.run_summaries(datetime.datetime(2026, 12, 1, 6, 30), runs=2, fail_first=True)
		self.assertEqual(sent, [("ISB", "isb.summary@example.com")])

	def test_statements_wait_for_the_stores_own_month_to_end(self):
		from field_sales.commission import statements
		from field_sales.utils.store import current_store

		# Site 1 Dec 08:00: store A is in December, store B still on 30 November.
		seen = []
		with (
			patch.object(statements, "commission_enabled", return_value=True),
			patch.object(
				statements,
				"store_filters",
				side_effect=lambda: seen.append(current_store()) or {"store": current_store()},
			),
		):
			statements.generate_monthly_statements()
		self.assertIn("ISA", seen)
		self.assertNotIn("ISB", seen)

	# -- Step 3: every amount carries its store's currency ------------------------------

	def give_currencies(self):
		for code in ("USD", "EUR"):
			if not frappe.db.exists("Currency", code):
				frappe.get_doc({"doctype": "Currency", "currency_name": code, "enabled": 1}).insert()
		frappe.db.set_value("Store", "ISA", "shop_currency", "INR")
		frappe.db.set_value("Store", "ISB", "shop_currency", "USD")

	def test_new_records_take_their_stores_currency(self):
		from field_sales.utils.money import fill_currency, record_currency, store_currency

		self.give_currencies()
		self.assertEqual(store_currency("ISB"), "USD")
		doc = frappe.get_doc({"doctype": "Platform Product", "store": "ISB"})
		fill_currency(doc)
		self.assertEqual(doc.currency, "USD")
		# An existing record keeps the currency it was made in.
		self.assertEqual(record_currency(frappe._dict(currency="INR", store="ISB")), "INR")

	def test_a_stores_currency_is_fixed_once_it_has_orders(self):
		from field_sales.sync.shop_settings import sync_shop_settings
		from field_sales.utils.store import use_store

		self.give_currencies()
		with use_store("ISB"):
			result = sync_shop_settings({"currency": "EUR", "timezone": "America/New_York", "country": "US"})
		self.assertEqual(frappe.db.get_value("Store", "ISB", "shop_currency"), "USD")
		self.assertEqual(len(result["mismatch"]), 1)
		self.assertIn("EUR", result["mismatch"][0])

	def test_an_order_is_not_pushed_in_another_currency(self):
		from field_sales.connectors import ConnectorError, get_connector

		self.give_currencies()
		conn = get_connector(require_enabled=False, store="ISB")
		conn._currency = "EUR"  # the shop's, already read
		with self.assertRaises(ConnectorError) as raised:
			conn._get_currency(frappe._dict(currency="USD"))
		self.assertEqual(raised.exception.code, "CURRENCY_MISMATCH")
		conn._currency = "USD"
		self.assertEqual(conn._get_currency(frappe._dict(currency="USD")), "USD")

	def test_a_vendor_report_covers_one_store(self):
		from field_sales.utils.store import report_store

		self.assertRaises(frappe.ValidationError, report_store, frappe._dict())
		self.assertEqual(report_store(frappe._dict(store="ISB")), "ISB")
		# A store's user always gets their own store, whatever they pick.
		self.assertEqual(report_store(frappe._dict(store="ISB"), user=self.a.manager), "ISA")

	def test_existing_records_get_their_stores_currency(self):
		from field_sales.patches.v2_0 import fill_record_currency

		self.give_currencies()
		frappe.db.set_value("Field Order", self.b.order, "currency", None)
		with patch.object(frappe.db, "commit"), patch.object(frappe, "reload_doc"):
			fill_record_currency.execute()
		self.assertEqual(frappe.db.get_value("Field Order", self.b.order, "currency"), "USD")

	def test_a_single_store_site_keeps_the_site_currency(self):
		from field_sales.utils.money import site_currency, store_currency

		self.give_currencies()
		self.multi.stop()
		try:
			self.assertEqual(store_currency("ISB"), site_currency())
		finally:
			self.multi.start()

	# -- Step 4: mail in the store's name, no merchant mail accounts, non-GST stores ------

	def test_store_mail_goes_out_in_the_stores_name(self):
		from frappe.email.doctype.email_account.email_account import EmailAccount

		from field_sales.utils.store import store_mail_args

		frappe.db.set_value(
			"Store", "ISB", {"company_name": "ISB Traders", "company_email": "orders@isb.example.com"}
		)
		vendor_account = frappe._dict(email_id="noreply@vendor.example.com")
		with patch.object(EmailAccount, "find_default_outgoing", return_value=vendor_account):
			args = store_mail_args("ISB")
			self.multi.stop()
			try:
				self.assertEqual(store_mail_args("ISB"), {})  # single-store sites: mail as before
			finally:
				self.multi.start()
		self.assertEqual(args["sender"], "ISB Traders <noreply@vendor.example.com>")
		self.assertEqual(args["reply_to"], "orders@isb.example.com")

	def test_merchants_cannot_add_mail_accounts_on_the_shared_site(self):
		from field_sales.shopify import merchant_settings

		self.assertRaises(frappe.PermissionError, merchant_settings._refuse_on_shared_site)
		self.multi.stop()
		try:
			merchant_settings._refuse_on_shared_site()  # allowed on single-store sites
		finally:
			self.multi.start()

	def test_a_non_gst_store_charges_no_tax(self):
		frappe.db.set_value("Store", "ISB", "tax_mode", "None")
		doc = frappe.get_doc("Field Order", self.b.order)
		doc.tax_percent = 18
		doc._apply_tax_mode()
		self.assertEqual(doc.tax_percent, 0)

		frappe.db.set_value("Store", "ISA", "tax_mode", "GST")
		doc = frappe.get_doc("Field Order", self.a.order)
		doc.tax_percent = 18
		doc._apply_tax_mode()
		self.assertEqual(doc.tax_percent, 18)


class TestStoreLifecycle(StoreIsolationCase):
	"""Phase 6: install, uninstall, shop/redact and reinstall of one store on the shared site."""

	SHOP_B = "isb-isolation.myshopify.com"

	def setUp(self):
		super().setUp()
		from field_sales.utils.store import set_current_store

		frappe.db.savepoint("store_lifecycle")
		self.addCleanup(set_current_store, None)
		self.multi = patch.dict(frappe.conf, MULTI)
		self.multi.start()
		self.addCleanup(self.multi.stop)

	def tearDown(self):
		frappe.db.rollback(save_point="store_lifecycle")
		super().tearDown()

	def install(self, shop):
		from field_sales.shopify import oauth
		from field_sales.utils.store import store_for_shop

		with patch.object(oauth, "save_oauth_tokens"), patch.object(oauth, "enqueue_in_store"):
			oauth.save_installation(shop, {"scope": ""})
		return store_for_shop(shop)

	def refused(self, user) -> bool:
		from field_sales.utils.store import StoreUninstalledError, refuse_uninstalled_store_user

		reset_request_caches()
		try:
			refuse_uninstalled_store_user(frappe._dict(user=user))
		except StoreUninstalledError:
			# Its own class (still an AuthenticationError): API clients tell it apart by exc_type.
			return True
		return False

	# -- install ------------------------------------------------------------------------

	def test_shops_installing_in_turn_get_a_store_each(self):
		first = self.install("islc-one.myshopify.com")
		second = self.install("islc-two.myshopify.com")
		self.assertTrue(first and second)
		self.assertNotEqual(first, second)

	def test_each_store_learns_its_shop_settings_whatever_the_match_flag(self):
		from field_sales.sync import shop_settings
		from field_sales.utils.store import use_store

		frappe.db.set_value("Store", "ISB", {"enabled": 1, "match_shop_settings": 0})
		with use_store("ISB"), patch.object(shop_settings, "sync_shop_settings", return_value={"changes": []}) as sync:
			shop_settings.scheduled_sync()
		sync.assert_called_once()

		# Saving the Store queues the sync too, while its currency is unknown.
		frappe.db.set_value("Store", "ISB", "shop_currency", None)
		doc = frappe.get_doc("Store", "ISB")
		with patch("field_sales.field_sales.doctype.store.store.enqueue_in_store") as enqueue:
			doc.save()
		self.assertEqual(enqueue.call_args.args[0], "field_sales.sync.shop_settings.scheduled_sync")
		self.assertEqual(enqueue.call_args.kwargs["store"], "ISB")

	def test_a_new_store_takes_its_tax_mode_from_the_shops_country(self):
		from field_sales.sync.shop_settings import sync_shop_settings
		from field_sales.utils.store import use_store

		shop = {"currency": "INR", "timezone": "Asia/Kolkata"}
		us_store = self.install("islc-us.myshopify.com")
		in_store = self.install("islc-in.myshopify.com")
		with use_store(us_store):
			sync_shop_settings({**shop, "country": "US"})
		with use_store(in_store):
			sync_shop_settings({**shop, "country": "IN"})
		self.assertEqual(frappe.db.get_value("Store", us_store, "tax_mode"), "None")
		self.assertEqual(frappe.db.get_value("Store", in_store, "tax_mode"), "GST")
		# A store with orders keeps the tax mode it has.
		frappe.db.set_value("Store", "ISB", "tax_mode", "GST")
		with use_store("ISB"):
			sync_shop_settings({**shop, "country": "US"})
		self.assertEqual(frappe.db.get_value("Store", "ISB", "tax_mode"), "GST")

	def test_records_made_before_the_store_currency_is_known_get_it_later(self):
		from field_sales.sync.shop_settings import sync_shop_settings
		from field_sales.utils.store import use_store

		if not frappe.db.exists("Currency", "USD"):
			frappe.get_doc({"doctype": "Currency", "currency_name": "USD", "enabled": 1}).insert()
		store = self.install("islc-early.myshopify.com")
		with use_store(store):
			product = frappe.get_doc(
				{
					"doctype": "Platform Product",
					"platform": "Shopify",
					"product_title": "Early product",
					"external_product_id": "8801",
					"external_variant_id": "8801",
					"status": "Active",
					"price": 10,
				}
			).insert(ignore_permissions=True)
			# Never the site's currency while the store's is unknown.
			self.assertFalse(product.currency)
			sync_shop_settings({"currency": "USD", "timezone": "America/New_York", "country": "US"})
		self.assertEqual(frappe.db.get_value("Platform Product", product.name, "currency"), "USD")

	def test_a_merchants_tax_mode_choice_survives_later_syncs(self):
		from field_sales.sync.shop_settings import sync_shop_settings
		from field_sales.utils.store import use_store

		shop = {"currency": "INR", "timezone": "Asia/Kolkata", "country": "IN"}
		store = self.install("islc-choice.myshopify.com")
		with use_store(store):
			sync_shop_settings(shop)  # first sync: GST from the country
			self.assertEqual(frappe.db.get_value("Store", store, "tax_mode"), "GST")
			# The merchant (not GST-registered) chooses no tax in Settings.
			doc = frappe.get_doc("Store", store)
			doc.tax_mode = "None"
			doc.save(ignore_permissions=True)
			sync_shop_settings(shop)  # the daily sync
		self.assertEqual(frappe.db.get_value("Store", store, "tax_mode"), "None")

	def test_a_tax_choice_made_before_the_first_successful_sync_is_kept(self):
		from field_sales.sync.shop_settings import sync_shop_settings
		from field_sales.utils.store import use_store

		# The install's shop settings sync failed, so the store has no country yet.
		store = self.install("islc-early-choice.myshopify.com")
		self.assertFalse(frappe.db.get_value("Store", store, "shop_country"))
		doc = frappe.get_doc("Store", store)
		doc.tax_mode = "None"
		doc.save(ignore_permissions=True)
		with use_store(store):
			sync_shop_settings({"currency": "INR", "timezone": "Asia/Kolkata", "country": "IN"})
		self.assertEqual(frappe.db.get_value("Store", store, "tax_mode"), "None")

	def test_a_new_shop_installs_from_the_shopify_admin_as_guest(self):
		from field_sales.shopify import embedded, oauth
		from field_sales.utils.store import store_for_shop

		# The embedded first load: no signed-in user, a valid Shopify token, no Store yet.
		shop = "islc-guest.myshopify.com"
		self.as_user("Guest")
		with (
			patch.object(embedded, "_exchange", return_value={"access_token": "t", "scope": ""}),
			patch.object(oauth, "save_oauth_tokens"),
			patch.object(oauth, "enqueue_in_store"),
			patch.object(embedded, "safe_commit"),
			patch("field_sales.connectors.shopify.safe_commit", create=True),
		):
			embedded.ensure_offline_token(shop, "id-token")
		store = store_for_shop(shop)
		self.assertTrue(store)
		# Its install is logged in its own store, though no user is signed in.
		self.assertTrue(frappe.db.exists("Sync Log", {"store": store, "operation": "Install", "status": "Success"}))

	# -- uninstall, redact, reinstall ---------------------------------------------------

	def test_users_of_an_uninstalled_store_cannot_sign_in(self):
		from field_sales.shopify.webhooks import handle_uninstalled
		from field_sales.utils.store import use_store

		with use_store("ISB"):
			handle_uninstalled(self.SHOP_B)
		self.assertTrue(self.refused(self.b.manager))
		self.assertFalse(self.refused(self.a.manager))
		self.assertFalse(self.refused(self.vendor))
		self.multi.stop()
		try:
			self.assertFalse(self.refused(self.b.manager))  # single-store sites: as before
		finally:
			self.multi.start()

		# Reinstalling lets them back in.
		self.install(self.SHOP_B)
		self.assertFalse(self.refused(self.b.manager))

	def test_shop_redact_disables_only_that_stores_users(self):
		from field_sales.shopify.privacy import shop_redact

		result = shop_redact(frappe._dict(name="PRIV-TEST", store="ISB"), {})
		self.assertGreater(result["users_disabled"], 0)
		self.assertEqual(frappe.db.get_value("User", self.b.manager, "enabled"), 0)
		self.assertEqual(frappe.db.get_value("User", self.a.manager, "enabled"), 1)
		self.assertEqual(frappe.db.get_value("User", self.vendor, "enabled"), 1)

	def test_a_reinstall_after_redaction_signs_the_merchant_in_again(self):
		from field_sales.shopify.embedded import embedded_email, get_embedded_user
		from field_sales.shopify.privacy import shop_redact
		from field_sales.utils.store import use_store

		frappe.db.set_value("Store", "ISB", "embedded_user", None)
		with use_store("ISB"), patch("field_sales.shopify.embedded.safe_commit"):
			user = get_embedded_user()
		self.assertEqual(user, embedded_email("ISB"))
		shop_redact(frappe._dict(name="PRIV-TEST", store="ISB"), {})
		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 0)

		with use_store("ISB"), patch("field_sales.shopify.embedded.safe_commit"):
			self.assertEqual(get_embedded_user(), user)
		self.assertEqual(frappe.db.get_value("User", user, "enabled"), 1)


class TestVendorStoreSwitch(StoreIsolationCase):
	"""Phase 7: vendor staff work in one store at a time in the web app (design Q2)."""

	def setUp(self):
		super().setUp()
		frappe.db.savepoint("vendor_switch")
		self.multi = patch.dict(frappe.conf, MULTI)
		self.multi.start()
		self.addCleanup(self.multi.stop)
		self.as_user(self.vendor)
		self.addCleanup(self.leave_store)

	def leave_store(self):
		from field_sales.utils.store import set_vendor_store

		self.as_user(self.vendor)
		set_vendor_store(None)

	def tearDown(self):
		frappe.db.rollback(save_point="vendor_switch")
		super().tearDown()

	def test_a_single_store_session_is_not_the_shared_site(self):
		from field_sales.frontend_api import get_session

		with patch.dict(frappe.conf, {"field_sales_multi_store": 0}):
			self.as_user(self.a.admin)
			self.assertFalse(get_session()["shared_site"])

	def test_vendor_staff_start_without_a_store(self):
		from field_sales.frontend_api import get_session

		session = get_session()
		self.assertTrue(session["is_vendor"])
		self.assertTrue(session["shared_site"])
		self.assertEqual(session["store"], {})
		self.assertIsNone(session["currency"])

	def test_a_switch_limits_the_session_to_that_store_and_is_recorded(self):
		from field_sales.frontend_api import get_session, switch_store
		from field_sales.utils.store import get_store_name, store_scope

		switch_store("ISB")
		reset_request_caches()
		self.assertEqual(store_scope(), "ISB")
		self.assertEqual(get_store_name(), "ISB")
		self.assertEqual(self.stores_seen("Field Order"), {"ISB"})
		self.assertEqual(get_session()["store"]["code"], "ISB")
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Store", "reference_name": "ISB", "content": ("like", f"%{self.vendor}%")},
			)
		)
		# Another user is not affected, and leaving brings back every store.
		self.assertEqual(store_scope(self.a.manager), "ISA")
		switch_store()
		reset_request_caches()
		self.assertIsNone(store_scope())

	def test_only_vendor_staff_on_the_shared_site_can_switch(self):
		from field_sales.frontend_api import get_vendor_stores, switch_store

		self.assertIn("ISB", [s.name for s in get_vendor_stores()])
		self.as_user(self.a.admin)
		self.assertRaises(frappe.PermissionError, switch_store, "ISB")
		self.assertRaises(frappe.PermissionError, get_vendor_stores)
		self.as_user(self.vendor)
		self.multi.stop()
		try:
			self.assertRaises(frappe.PermissionError, switch_store, "ISB")
		finally:
			self.multi.start()

	def test_the_session_tells_the_app_its_currency_and_tax_mode(self):
		from field_sales.frontend_api import get_session

		frappe.db.set_value("Store", "ISA", {"shop_currency": "INR", "tax_mode": "None"})
		self.as_user(self.a.manager)
		session = get_session()
		self.assertEqual(session["currency"], "INR")
		self.assertEqual(session["store"]["tax_mode"], "None")
		self.assertFalse(session["is_vendor"])

	def test_a_store_admin_signed_in_with_a_password_reaches_the_store_settings(self):
		from field_sales.shopify import merchant_settings

		# On the shared site Settings always uses the merchant endpoints (no Shopify token here).
		self.as_user(self.a.admin)
		settings = merchant_settings.get_store_settings()
		self.assertTrue(settings)
		# A plain manager without the shop's session token still cannot.
		self.as_user(self.a.manager)
		self.assertRaises(frappe.PermissionError, merchant_settings.get_store_settings)


class TestStoreRecordAccess(StoreIsolationCase):
	"""The Store records themselves (and merchant settings): only vendor staff and the store's own users."""

	def setUp(self):
		super().setUp()
		frappe.db.savepoint("store_record_access")
		self.multi = patch.dict(frappe.conf, MULTI)
		self.multi.start()
		self.addCleanup(self.multi.stop)

	def tearDown(self):
		frappe.db.rollback(save_point="store_record_access")
		super().tearDown()

	def test_a_store_admin_in_no_store_reaches_no_store(self):
		from frappe.client import get as client_get

		from field_sales.shopify import merchant_settings

		self.as_user(make_user("isx.unassigned.admin@example.com", ["Store Admin"]))
		for store in ("ISA", "ISB"):
			with self.subTest(store=store):
				self.assertFalse(frappe.has_permission("Store", "read", store))
				self.assertFalse(frappe.has_permission("Store", "write", store))
				self.assertRaises(frappe.PermissionError, client_get, "Store", store)
		self.assertEqual(frappe.get_list("Store", pluck="name"), [])
		self.assertRaises((frappe.PermissionError, frappe.ValidationError), merchant_settings.get_store_settings)

	def test_a_store_admin_reaches_only_their_own_store(self):
		self.as_user(self.a.admin)
		self.assertTrue(frappe.has_permission("Store", "write", "ISA"))
		self.assertFalse(frappe.has_permission("Store", "read", "ISB"))
		self.assertEqual(frappe.get_list("Store", pluck="name"), ["ISA"])

	def test_vendor_staff_reach_every_store(self):
		self.as_user(self.vendor)
		self.assertTrue(frappe.has_permission("Store", "write", "ISB"))
		self.assertTrue({"ISA", "ISB"} <= set(frappe.get_list("Store", pluck="name")))


class TestStoreAdminOnboarding(StoreIsolationCase):
	"""After a Shopify install the merchant names the store's admin, who gets an invitation."""

	def setUp(self):
		super().setUp()
		from frappe.email.doctype.email_account.email_account import EmailAccount

		from field_sales.utils.store import set_current_store

		frappe.db.savepoint("admin_onboarding")
		self.addCleanup(set_current_store, None)
		for p in (
			patch.dict(frappe.conf, MULTI),
			patch.object(
				EmailAccount, "find_default_outgoing", return_value=frappe._dict(email_id="noreply@vendor.example.com")
			),
			patch("field_sales.shopify.embedded.safe_commit"),
		):
			p.start()
			self.addCleanup(p.stop)
		self.store = self.install_new_shop("isob-owner.myshopify.com")

	def tearDown(self):
		frappe.db.rollback(save_point="admin_onboarding")
		super().tearDown()

	def install_new_shop(self, shop):
		from field_sales.shopify import oauth
		from field_sales.utils.store import store_for_shop

		with patch.object(oauth, "save_oauth_tokens"), patch.object(oauth, "enqueue_in_store"):
			oauth.save_installation(shop, {"scope": ""})
		frappe.db.set_value(
			"Store", store_for_shop(shop), {"company_name": "Owner Traders", "company_email": "sales@owner.example.com"}
		)
		return store_for_shop(shop)

	def as_shopify_admin(self):
		"""Signed in as the install's automatic user, as the Shopify admin first is."""
		from field_sales.shopify.embedded import get_embedded_user
		from field_sales.utils.store import use_store

		self.as_user("Administrator")
		with use_store(self.store):
			user = get_embedded_user()
		self.as_user(user)
		return user

	def set_admin(self, email):
		from field_sales.shopify import store_admin

		with patch.object(store_admin.frappe, "sendmail") as sendmail:
			result = store_admin.set_store_admin(email)
		return result, sendmail

	def test_a_new_store_asks_for_its_admin_and_invites_them(self):
		from field_sales.shopify.embedded import get_embedded_user
		from field_sales.shopify.store_admin import needs_admin_setup
		from field_sales.utils.store import use_store, user_store

		self.as_shopify_admin()
		self.assertTrue(needs_admin_setup(self.store))

		result, sendmail = self.set_admin("Owner.Onboard@example.com")
		email = "owner.onboard@example.com"
		self.assertEqual(result, {"email": email, "invited": True})

		roles = set(frappe.get_roles(email))
		self.assertTrue({"Store Admin", "Sales Manager", "Field Sales Rep"} <= roles, roles)
		reset_request_caches()
		self.assertEqual(user_store(email), self.store)
		rep = frappe.db.get_value("Sales Rep", {"user": email}, ["store", "sales_manager", "sales_rep"], as_dict=True)
		self.assertEqual((rep.store, rep.sales_manager, rep.sales_rep), (self.store, 1, 1))

		mail = sendmail.call_args.kwargs
		self.assertEqual(mail["recipients"], [email])
		self.assertEqual(mail["template"], "store_admin_invite")
		self.assertIn("/update-password?key=", mail["args"]["link"])
		# Sent by Field Sales itself (the site's default account): no store sender, no store reply-to.
		self.assertNotIn("sender", mail)
		self.assertNotIn("reply_to", mail)
		self.assertEqual(mail["args"]["store_name"], "Owner Traders")

		self.assertFalse(needs_admin_setup(self.store))
		self.as_user("Administrator")
		with use_store(self.store):
			self.assertEqual(get_embedded_user(), email)

	def test_only_the_stores_admin_may_name_it_and_never_with_a_foreign_account(self):
		from field_sales.shopify import store_admin

		self.as_shopify_admin()
		for email in (self.b.manager, self.vendor, "Administrator"):
			with self.subTest(email=email), self.assertRaises(frappe.ValidationError):
				self.set_admin(email)
		# A manager of store A (no Store Admin role) cannot name store A's admin.
		self.as_user(self.a.manager)
		self.assertRaises(frappe.PermissionError, store_admin.set_store_admin, "someone@example.com")

	def test_setting_a_password_from_the_link_leads_to_the_login_page(self):
		from unittest.mock import MagicMock

		from field_sales.shopify import store_admin

		frappe.local.login_manager = MagicMock()
		self.addCleanup(setattr, frappe.local, "login_manager", None)
		with patch.object(store_admin, "_frappe_update_password", return_value="/desk"):
			self.as_user(self.a.rep_user)
			self.assertEqual(store_admin.update_password("N3w-pass!", key="k"), store_admin.LOGIN_AFTER_PASSWORD)
			frappe.local.login_manager.logout.assert_called_once()
			# A password change while signed in, and vendor staff, keep Frappe's own result.
			self.assertEqual(store_admin.update_password("N3w-pass!", old_password="old"), "/desk")
			self.as_user(self.vendor)
			self.assertEqual(store_admin.update_password("N3w-pass!", key="k"), "/desk")

	def test_after_redaction_the_store_asks_again_and_the_same_email_comes_back(self):
		from field_sales.shopify.privacy import shop_redact
		from field_sales.shopify.store_admin import needs_admin_setup

		self.as_shopify_admin()
		self.set_admin("owner.again@example.com")
		self.as_user("Administrator")
		shop_redact(frappe._dict(name="PRIV-TEST", store=self.store), {})
		self.assertEqual(frappe.db.get_value("User", "owner.again@example.com", "enabled"), 0)

		self.as_shopify_admin()  # reinstalled: the automatic user signs in again
		self.assertTrue(needs_admin_setup(self.store))
		self.set_admin("owner.again@example.com")
		self.assertEqual(frappe.db.get_value("User", "owner.again@example.com", "enabled"), 1)

	def test_a_single_store_site_is_never_asked(self):
		from field_sales.shopify.store_admin import needs_admin_setup

		with patch.dict(frappe.conf, {"field_sales_multi_store": 0}):
			self.assertFalse(needs_admin_setup(self.store))

	def test_the_store_forms_buttons_act_on_that_store(self):
		from unittest.mock import MagicMock

		from field_sales import api
		from field_sales.utils.store import current_store

		used = []

		def connector(*args, **kwargs):
			used.append(current_store())
			conn = MagicMock()
			conn.test_connection.return_value = {"ok": False, "message": "test"}
			conn.get_locations.return_value = [{"id": "1", "name": "Main"}]
			return conn

		with patch.object(api, "get_connector", side_effect=connector):
			# Vendor staff (no store of their own) on Store B's form.
			self.as_user(self.vendor)
			api.test_connection(store="ISB")
			api.get_locations(store="ISB")
			self.assertEqual(used, ["ISB", "ISB"])
			# A store's admin: their own store's form only.
			self.as_user(self.a.admin)
			self.assertRaises(frappe.PermissionError, api.test_connection, store="ISB")
			used.clear()
			api.test_connection()  # no store: the user's own (resolved by get_connector), never B
			self.assertNotIn("ISB", used)

	def test_an_existing_account_of_no_store_cannot_be_made_the_admin(self):
		from field_sales.utils.store import user_store

		# Support staff (no store, no vendor role) and a disabled website user: naming either
		# would let the store's Shopify admins sign in as them and move them into the store.
		staff = make_user("isob.support@example.com", ["Sales Manager"])
		disabled = make_user("isob.disabled@example.com", ["Field Sales Rep"])
		frappe.db.set_value("User", disabled, "enabled", 0)
		self.as_shopify_admin()
		for email in (staff, disabled):
			with self.subTest(email=email), self.assertRaises(frappe.ValidationError):
				self.set_admin(email)
			reset_request_caches()
			self.assertIsNone(user_store(email))
			self.assertNotIn("Store Admin", frappe.get_roles(email))
		self.assertEqual(frappe.db.get_value("User", disabled, "enabled"), 0)

	def test_the_first_admin_retires_the_automatic_user(self):
		from field_sales.shopify import billing
		from field_sales.shopify.embedded import embedded_email, get_embedded_user
		from field_sales.utils.limits import SALES_REPS
		from field_sales.utils.store import use_store

		automatic = self.as_shopify_admin()
		self.assertEqual(automatic, embedded_email(self.store))
		self.set_admin("owner.retire@example.com")

		self.as_user("Administrator")
		roles = set(frappe.get_roles(automatic))
		self.assertFalse({"Store Admin", "Sales Manager"} & roles, roles)
		self.assertEqual(frappe.db.get_value("Sales Rep", {"user": automatic}, "active"), 0)
		with use_store(self.store):
			# Neither the admin (the embedded user) nor the retired automatic rep is counted.
			self.assertEqual(billing.usage(SALES_REPS), 0)

		# The admin unusable (as after shop/redact): Shopify signs in as the automatic user again.
		frappe.db.set_value("User", "owner.retire@example.com", "enabled", 0)
		reset_request_caches()
		with use_store(self.store):
			self.assertEqual(get_embedded_user(), automatic)
		self.assertTrue({"Store Admin", "Sales Manager"} <= set(frappe.get_roles(automatic)))
		self.assertEqual(frappe.db.get_value("Sales Rep", {"user": automatic}, "active"), 1)

	def test_a_paused_store_stays_off_when_the_app_is_opened(self):
		from field_sales.shopify import embedded, oauth

		self.as_user(self.vendor)
		doc = frappe.get_doc("Store", self.store)
		doc.enabled = 0
		doc.save()
		self.assertEqual(frappe.db.get_value("Store", self.store, "suspended"), 1)

		self.as_user("Administrator")
		shop = frappe.db.get_value("Store", self.store, "store_url")
		with patch.object(embedded, "_exchange") as exchange:
			with self.assertRaises(embedded.ConnectError):
				embedded.ensure_offline_token(shop, "id-token")
			exchange.assert_not_called()
		# A reinstall (OAuth callback) keeps it off too.
		with patch.object(oauth, "save_oauth_tokens"), patch.object(oauth, "enqueue_in_store"):
			oauth.save_installation(shop, {"scope": ""})
		self.assertEqual(frappe.db.get_value("Store", self.store, "enabled"), 0)

		# Vendor staff clear the pause: the next install turns it back on.
		self.as_user(self.vendor)
		doc = frappe.get_doc("Store", self.store)
		doc.suspended = 0
		doc.save()
		self.as_user("Administrator")
		with patch.object(oauth, "save_oauth_tokens"), patch.object(oauth, "enqueue_in_store"):
			oauth.save_installation(shop, {"scope": ""})
		self.assertEqual(frappe.db.get_value("Store", self.store, "enabled"), 1)

	def test_an_automatic_disable_still_reconnects(self):
		from field_sales.shopify import oauth
		from field_sales.utils.store import set_store_value

		# As a failed token refresh or an uninstall does: written directly, no pause.
		set_store_value("enabled", 0, store=self.store)
		shop = frappe.db.get_value("Store", self.store, "store_url")
		with patch.object(oauth, "save_oauth_tokens"), patch.object(oauth, "enqueue_in_store"):
			oauth.save_installation(shop, {"scope": ""})
		self.assertEqual(frappe.db.get_value("Store", self.store, ["enabled", "suspended"]), (1, 0))

	def test_a_merchant_cannot_lift_a_pause(self):
		frappe.db.set_value("Store", self.store, {"enabled": 0, "suspended": 1})
		self.as_shopify_admin()
		doc = frappe.get_doc("Store", self.store)
		doc.suspended = 0
		self.assertRaises(frappe.PermissionError, doc.save, ignore_permissions=True)

	def test_a_disabled_user_of_the_store_comes_back_only_as_its_former_admin(self):
		rep = make_user("isob.former-rep@example.com", ["Field Sales Rep"])
		assign_user_to_store(rep, self.store)
		frappe.db.set_value("User", rep, "enabled", 0)
		self.as_shopify_admin()
		self.assertRaises(frappe.ValidationError, self.set_admin, rep)
		self.assertEqual(frappe.db.get_value("User", rep, "enabled"), 0)

	def test_a_store_user_holding_a_site_wide_role_cannot_be_the_admin(self):
		user = make_user("isob.sysman@example.com", ["Sales Manager", "System Manager"])
		assign_user_to_store(user, self.store)
		self.as_shopify_admin()
		self.assertRaises(frappe.ValidationError, self.set_admin, user)

	def test_naming_the_admin_keeps_the_callers_session(self):
		self.as_shopify_admin()
		# A real request's session (frappe.set_user in a test leaves sid = the user's name).
		frappe.session.sid = "isob-session-sid"
		frappe.session.data = frappe._dict(csrf_token="isob-csrf")
		session = frappe.session.user, frappe.session.sid, dict(frappe.session.data)
		frappe.local.form_dict = frappe._dict(email="owner.session@example.com")
		self.set_admin("owner.session@example.com")
		self.assertEqual((frappe.session.user, frappe.session.sid, dict(frappe.session.data)), session)
		self.assertEqual(frappe.local.form_dict.email, "owner.session@example.com")

	def test_a_changed_admin_is_no_longer_store_admin(self):
		self.as_shopify_admin()
		self.set_admin("owner.first@example.com")
		self.as_user("owner.first@example.com")
		self.set_admin("owner.second@example.com")
		self.assertNotIn("Store Admin", frappe.get_roles("owner.first@example.com"))
		# Still a manager of the store.
		self.assertIn("Sales Manager", frappe.get_roles("owner.first@example.com"))
		self.assertIn("Store Admin", frappe.get_roles("owner.second@example.com"))


class TestSharedSiteVendorRule(StoreIsolationCase):
	"""On the shared site only the vendor role makes a user of no store vendor staff."""

	def setUp(self):
		super().setUp()
		multi = patch.dict(frappe.conf, MULTI)
		multi.start()
		self.addCleanup(multi.stop)

	def test_a_system_manager_of_no_store_reaches_no_store(self):
		user = make_user("isolation.sysman-only@example.com", ["System Manager"])
		self.as_user(user)
		self.assertFalse(is_vendor())
		self.assertEqual(store_scope(), "")
		self.assertEqual(frappe.get_list("Retailer", pluck="name"), [])

	def test_the_vendor_role_reaches_every_store(self):
		self.as_user(self.vendor)
		self.assertTrue(is_vendor())
		self.assertIsNone(store_scope())

	def test_an_unassigned_user_never_falls_back_to_the_only_store(self):
		from field_sales.utils.store import get_store_name

		self.as_user(make_user("isolation.unassigned@example.com", ["Sales Manager"]))
		# Before: the only Store, or NoStoreContext with several (as here). Now: none at all.
		self.assertIsNone(get_store_name())
		self.assertRaises(frappe.PermissionError, get_store_name, required=True)

	def test_vendor_alerts_go_to_vendor_staff_only(self):
		from field_sales.sync.notify import clear_role_users_cache, users_with_roles

		make_user("isolation.sysman-only@example.com", ["System Manager"])
		clear_role_users_cache()
		vendors = users_with_roles(("System Manager",), vendor=True)
		self.assertIn(self.vendor, vendors)
		self.assertNotIn("isolation.sysman-only@example.com", vendors)

	def test_products_of_two_stores_may_share_a_variant_id(self):
		names = []
		for store in ("ISA", "ISB"):
			doc = frappe.get_doc(
				{
					"doctype": "Platform Product",
					"platform": "Shopify",
					"external_variant_id": "isolation-shared-variant",
					"external_product_id": "isolation-shared-product",
					"product_title": "Shared",
					"store": store,
				}
			).insert(ignore_permissions=True)
			names.append(doc.name)
		self.assertEqual(names, ["Shopify-ISA-isolation-shared-variant", "Shopify-ISB-isolation-shared-variant"])
