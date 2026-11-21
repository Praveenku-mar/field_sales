# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import get_decrypted_password, set_encrypted_password

from field_sales.patches.v2_0 import assign_users_to_store, create_first_store
from field_sales.payments.razorpay import razorpay_enabled
from field_sales.shopify.embedded import embedded_email
from field_sales.tests.store_fixtures import make_user
from field_sales.utils.store import (
	NoStoreContext,
	clear_store_cache,
	clear_user_store_cache,
	current_store,
	get_store,
	get_store_name,
	invoice_series,
	make_store_code,
	order_series,
	use_store,
	user_store,
)

MULTI = {"field_sales_multi_store": 1}


def new_store(code: str, **values):
	doc = frappe.get_doc(
		{
			"doctype": "Store",
			"store_code": code,
			"platform": "Shopify",
			"store_url": f"{code.lower().replace('-', '')}-test.myshopify.com",
			**values,
		}
	)
	return doc.insert(ignore_permissions=True)


class TestStore(IntegrationTestCase):
	def setUp(self):
		clear_store_cache()

	def tearDown(self):
		frappe.db.rollback()
		clear_store_cache()

	def test_a_location_cannot_be_listed_twice(self):
		settings = get_store()
		settings.set("locations", [])
		for _i in range(2):
			settings.append("locations", {"location_id": "1001", "location_name": "Main", "sell_from": 1})
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	# -- store code ----------------------------------------------------------

	def test_code_is_upper_cased_and_names_the_store(self):
		self.assertEqual(new_store("ab1").name, "AB1")

	def test_code_must_be_two_to_four_letters_or_digits(self):
		for bad in ("A", "ABCDE", "A-B"):
			with self.assertRaises(frappe.ValidationError):
				new_store(bad)

	def test_code_can_change_until_the_first_invoice(self):
		new_store("OLD")
		frappe.rename_doc("Store", "OLD", "NEW", force=True)
		self.assertEqual(frappe.db.get_value("Store", "NEW", "store_code"), "NEW")

		frappe.db.sql(
			"insert into `tabField Order` (name, store, invoice_no) values ('FO-TEST-INV', 'NEW', 'INV-1')"
		)
		with self.assertRaisesRegex(frappe.ValidationError, "issued invoices"):
			frappe.rename_doc("Store", "NEW", "NEW2", force=True)

	def test_make_store_code_skips_taken_codes(self):
		new_store("ACME")
		self.assertEqual(make_store_code("acme.myshopify.com"), "ACM2")
		self.assertEqual(make_store_code("x"), "XS")

	# -- which store ---------------------------------------------------------

	def test_the_only_store_is_used_without_context(self):
		only = frappe.get_all("Store", pluck="name")
		self.assertEqual(len(only), 1)
		self.assertEqual(get_store_name(), only[0])

	def test_several_stores_need_a_context(self):
		new_store("TWO")
		clear_store_cache()
		with self.assertRaises(NoStoreContext):
			get_store_name()
		with use_store("TWO"):
			self.assertEqual(get_store_name(), "TWO")
			self.assertEqual(current_store(), "TWO")
		self.assertIsNone(current_store())

	def test_new_records_get_the_current_store(self):
		log = frappe.get_doc({"doctype": "Sync Log", "operation": "Product Sync", "status": "Success"})
		log.insert(ignore_permissions=True)
		self.assertEqual(log.store, get_store_name())

		new_store("TWO")
		with use_store("TWO"):
			other = frappe.get_doc({"doctype": "Sync Log", "operation": "Product Sync", "status": "Success"})
			other.insert(ignore_permissions=True)
		self.assertEqual(other.store, "TWO")

	# -- shared site (field_sales_multi_store) ------------------------------

	def test_numbers_per_store_only_on_the_shared_site(self):
		self.assertIsNone(order_series("ACME"))
		self.assertEqual(invoice_series("ACME", "INV-.YYYY.-.#####"), "INV-.YYYY.-.#####")
		with patch.dict(frappe.conf, MULTI):
			self.assertEqual(order_series("ACME"), "FO-ACME-.YYYY.-.#####")
			series = invoice_series("ACME", "INV-.YYYY.-.#####")
			self.assertEqual(series, "ACME/.YY./.#####")
			# GST: an invoice number is at most 16 characters.
			self.assertLessEqual(len("ACME/26/00001"), 16)

	def test_shared_site_sends_drafts_only(self):
		store = get_store()
		store.order_mode = "Confirmed"
		with patch.dict(frappe.conf, MULTI), self.assertRaisesRegex(frappe.ValidationError, "Draft"):
			store.save(ignore_permissions=True)

	def test_shared_site_has_no_razorpay(self):
		frappe.db.set_single_value("Razorpay Settings", "enabled", 1)
		self.assertTrue(razorpay_enabled())
		with patch.dict(frappe.conf, MULTI):
			self.assertFalse(razorpay_enabled())


class TestCreateFirstStorePatch(IntegrationTestCase):
	def setUp(self):
		clear_store_cache()

	def tearDown(self):
		frappe.db.rollback()
		clear_store_cache()

	def test_rerun_only_fills_records_without_a_store(self):
		store = get_store_name()
		log = frappe.get_doc({"doctype": "Sync Log", "operation": "Product Sync", "status": "Success"})
		log.insert(ignore_permissions=True)
		frappe.db.set_value("Sync Log", log.name, "store", None)

		create_first_store.execute()

		self.assertEqual(frappe.db.count("Store"), 1)
		self.assertEqual(frappe.db.get_value("Sync Log", log.name, "store"), store)

	def test_store_connection_becomes_the_first_store(self):
		frappe.db.delete("Store")
		frappe.db.delete("Store Location", {"parenttype": "Store"})
		clear_store_cache()
		source = "Store Connection"
		frappe.db.delete("Singles", {"doctype": source})
		for field, value in {
			"store_url": "acme-co.myshopify.com",
			"shop_name": "Acme Co",
			"enabled": "1",
			"order_mode": "Confirmed",
			"failed_log_retention_days": "90",
		}.items():
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values (%s, %s, %s)", (source, field, value)
			)
		set_encrypted_password(source, source, "tok-123", "access_token")
		frappe.get_doc(
			{
				"doctype": "Store Location",
				"parent": source,
				"parenttype": source,
				"parentfield": "locations",
				"location_id": "L1",
				"location_name": "Main",
				"sell_from": 1,
			}
		).db_insert()

		create_first_store.execute()

		store = frappe.get_doc("Store", "ACME")
		self.assertEqual(store.store_url, "acme-co.myshopify.com")
		self.assertEqual(store.order_mode, "Confirmed")
		self.assertEqual(store.enabled, 1)
		self.assertEqual([r.location_id for r in store.locations], ["L1"])
		self.assertEqual(get_decrypted_password("Store", "ACME", "access_token"), "tok-123")
		# Only the vendor-wide values stay on Store Connection.
		left = frappe.db.sql("select field from `tabSingles` where doctype = %s", source, pluck=True)
		self.assertIn("failed_log_retention_days", left)
		self.assertNotIn("store_url", left)
		self.assertFalse(frappe.db.exists("__Auth", {"doctype": source}))


class TestAssignUsersPatch(IntegrationTestCase):
	def setUp(self):
		clear_store_cache()
		clear_user_store_cache()

	def tearDown(self):
		frappe.db.rollback()
		clear_store_cache()
		clear_user_store_cache()

	def test_the_users_of_a_single_store_site_join_its_store(self):
		rep = make_user("assign.rep@example.com", ["Field Sales Rep"])
		manager = make_user("assign.manager@example.com", ["Sales Manager"])
		sysman = make_user("assign.sysman@example.com", ["System Manager"])
		frappe.db.delete("User Permission", {"allow": "Store", "user": ("in", (rep, manager, sysman))})
		clear_user_store_cache()

		assign_users_to_store.execute()
		assign_users_to_store.execute()  # safe to repeat

		store = get_store_name()
		self.assertEqual(user_store(rep), store)
		self.assertEqual(user_store(manager), store)
		self.assertEqual(frappe.db.count("User Permission", {"user": rep, "allow": "Store"}), 1)
		# A System Manager with no store stays vendor staff.
		self.assertIsNone(user_store(sysman))

	def test_the_embedded_user_gets_store_admin(self):
		store = get_store_name()
		email = embedded_email(store)
		make_user(email, ["Sales Manager"])
		frappe.db.set_value("Store", store, "embedded_user", email)
		assign_users_to_store.execute()
		self.assertIn("Store Admin", frappe.get_roles(email))

	def test_each_store_has_its_own_embedded_user_on_the_shared_site(self):
		with patch.dict(frappe.conf, MULTI):
			self.assertNotEqual(embedded_email("AAA"), embedded_email("BBB"))
		self.assertEqual(embedded_email("AAA"), embedded_email("BBB"))  # single-store: one login
