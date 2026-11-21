# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Two stores on one site: a user of store A reaches nothing of store B, whatever their role,
and vendor staff (a System Manager with no store) reach both.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.tests.store_fixtures import make_store_team, make_user, reset_request_caches
from field_sales.utils.store import (
	assign_user_to_store,
	clear_store_cache,
	is_vendor,
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
		cls.vendor = make_user("isolation.vendor@example.com", ["System Manager"])
		# A user of store B who is not a rep (yet): store A must not be able to adopt them.
		cls.b.spare = make_user("isb.spare@example.com", ["Field Sales Rep"])
		assign_user_to_store(cls.b.spare, "ISB")

	def setUp(self):
		frappe.set_user("Administrator")
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
		with self.assertRaises(frappe.ValidationError):
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
		for user in ("Administrator", self.b.manager):
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
