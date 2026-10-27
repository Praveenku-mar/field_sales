# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Retailers outside India: country, phone rules, store address payload, WhatsApp number, patch."""

from unittest.mock import patch

import frappe
from frappe.cache_manager import clear_defaults_cache
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from field_sales.connectors.shopify import (
	ShopifyConnector,
	_customer_address,
	_customer_input,
	province_code,
)
from field_sales.field_sales.doctype.retailer.retailer import get_default_country
from field_sales.frontend_api import get_order_payment_info
from field_sales.patches.v1_0 import retailer_country_india
from field_sales.sync.shop_settings import sync_shop_settings
from field_sales.utils import phone


class TestRetailerCountry(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Disabled: saving a Retailer then queues no store push.
		frappe.db.set_single_value(
			"Store Connection", {"enabled": 0, "shop_country": None, "match_shop_settings": 0}
		)
		# Reads below must see the values set here, not an earlier test's cached ones.
		frappe.db.value_cache.clear()
		# The site's global "country" default (from System Settings) pre-fills new retailers
		# inside insert(), before validate can apply the shop's country.
		frappe.db.delete("DefaultValue", {"defkey": "country"})
		clear_defaults_cache()

	def tearDown(self):
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def retailer(self, country=None, phone_no="98765 43210", **values):
		doc = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": f"Country Test {frappe.generate_hash(length=6)}",
				"phone": phone_no,
				"address_line1": "1 Main Street",
				"city": "Somewhere",
				"country": country,
				**values,
			}
		)
		return doc.insert(ignore_permissions=True)

	def assert_rejected(self, country, phone_no):
		with self.assertRaises(frappe.ValidationError, msg=f"{country} {phone_no}"):
			self.retailer(country, phone_no)

	# -- phones ------------------------------------------------------------

	def test_indian_numbers_behave_as_before(self):
		for value in ("98765 43210", "9876543210", "+91 98765 43210", "+91-98765-43210", "+919876543210"):
			self.retailer("India", value)
			self.retailer(None, value)  # retailers without a country keep the Indian rule
		for value in ("12345", "98765 4321", "0 98765 43210", "+1 201 555 0123", "98765432101"):
			self.assert_rejected("India", value)

	def test_us_uk_uae_numbers(self):
		valid = {
			"United States": ("+1 201-555-0123", "(201) 555-0123"),
			"United Kingdom": ("07400 123456", "+44 7400 123456"),
			"United Arab Emirates": ("050 123 4567", "+971 50 123 4567"),
		}
		for country, numbers in valid.items():
			for value in numbers:
				self.retailer(country, value)

		for country, value in (
			("United States", "555-0100"),
			("United Kingdom", "0740 012"),
			("United Arab Emirates", "123"),
			("United States", "98765 43210"),
		):
			self.assert_rejected(country, value)

	def test_error_message_shows_an_example(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self.retailer("United Kingdom", "12")
		self.assertIn("+44 7400 123456", str(ctx.exception))

	def test_phone_helpers(self):
		self.assertEqual(phone.to_e164("98765 43210", "IN"), "+919876543210")
		self.assertEqual(phone.to_e164("(201) 555-0123", "us"), "+12015550123")
		self.assertEqual(phone.to_e164("07400 123456", "GB"), "+447400123456")
		self.assertIsNone(phone.to_e164("123", "US"))
		self.assertEqual(phone.whatsapp_digits("050 123 4567", "AE"), "971501234567")
		self.assertIsNone(phone.whatsapp_digits("020 7946 0958", "GB"))  # London landline

	# -- store payload -----------------------------------------------------

	def test_address_payload_uses_retailer_country(self):
		cases = (
			("United States", "+1 201-555-0123", "California", "US", "CA", "+12015550123"),
			("India", "98765 43210", "Tamil Nadu", "IN", "TN", "+919876543210"),
			("United Kingdom", "07400 123456", "England", "GB", None, "+447400123456"),
			("United Arab Emirates", "050 123 4567", "Dubai", "AE", None, "+971501234567"),
		)
		for country, number, state, code, province, e164 in cases:
			doc = self.retailer(country, number, state=state)
			customer = _customer_input(doc)
			address = _customer_address(doc, customer.get("phone"))
			self.assertEqual(address["countryCode"], code, country)
			self.assertEqual(customer["phone"], e164, country)
			if province:
				self.assertEqual(address["provinceCode"], province)
				self.assertNotIn("province", address)
			else:
				self.assertEqual(address["province"], state)
				self.assertNotIn("provinceCode", address)

	def test_province_code_only_when_it_maps_cleanly(self):
		self.assertEqual(province_code("IN", "tamil nadu"), "TN")
		self.assertEqual(province_code("US", "CA"), "CA")
		self.assertIsNone(province_code("IN", "Madras State"))
		# Telangana is TS in Shopify; a pycountry that moved to ISO's newer TG must not send TG.
		self.assertIn(province_code("IN", "Telangana"), ("TS", None))
		self.assertIsNone(province_code("GB", "England"))

	# -- defaults, shop country, WhatsApp ----------------------------------

	def test_new_retailer_gets_shop_country(self):
		frappe.db.set_single_value("Store Connection", "shop_country", "AE")
		self.assertEqual(get_default_country(), "United Arab Emirates")
		doc = self.retailer(None, "050 123 4567")
		self.assertEqual(doc.country, "United Arab Emirates")

		frappe.db.set_single_value("Store Connection", "shop_country", None)
		self.assertEqual(
			get_default_country(), frappe.db.get_single_value("System Settings", "country") or None
		)

	def test_shop_country_is_fetched_and_saved(self):
		frappe.db.set_single_value(
			"Store Connection", {"platform": "Shopify", "store_url": "c.myshopify.com"}
		)
		data = {
			"shop": {
				"currencyCode": "AED",
				"ianaTimezone": "Asia/Dubai",
				"shopAddress": {"countryCodeV2": "AE"},
			}
		}
		with patch.object(ShopifyConnector, "_graphql", return_value=data):
			shop = ShopifyConnector(frappe.get_single("Store Connection")).get_shop_settings()
		self.assertEqual(shop, {"currency": "AED", "timezone": "Asia/Dubai", "country": "AE"})
		sync_shop_settings(shop)
		self.assertEqual(frappe.db.get_single_value("Store Connection", "shop_country"), "AE")

	def test_whatsapp_number_follows_retailer_country(self):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "country.rep@example.com",
				"first_name": "C",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		rep = frappe.get_doc({"doctype": "Sales Rep", "user": user.name}).insert(ignore_permissions=True)
		product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Country product",
				"external_product_id": "9800",
				"external_variant_id": "98",
				"status": "Active",
				"price": 10,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)

		for country, number, expected in (
			("India", "98765 43210", "919876543210"),
			("United Kingdom", "07400 123456", "447400123456"),
			("United States", "(201) 555-0123", "12015550123"),
		):
			retailer = self.retailer(country, number)
			order = frappe.get_doc(
				{
					"doctype": "Field Order",
					"retailer": retailer.name,
					"sales_rep": rep.name,
					"order_date": nowdate(),
					"items": [{"product": product.name, "qty": 1, "rate": 10}],
				}
			).insert(ignore_permissions=True)
			self.assertEqual(get_order_payment_info(order.name)["whatsapp_number"], expected, country)

	# -- patch ---------------------------------------------------------------

	def test_patch_sets_india_on_retailers_without_country(self):
		old = self.retailer(None)
		frappe.db.set_value("Retailer", old.name, "country", None)
		us = self.retailer("United States", "+1 201-555-0123")
		retailer_country_india.execute()
		self.assertEqual(frappe.db.get_value("Retailer", old.name, "country"), "India")
		self.assertEqual(frappe.db.get_value("Retailer", us.name, "country"), "United States")

	# -- invoice -----------------------------------------------------------

	def test_invoice_title_follows_gstin(self):
		html = frappe.get_doc("Print Format", "Field Sales Invoice").html
		self.assertIn('"TAX INVOICE" if gst else "INVOICE"', html)
		self.assertIn("{% elif gst %}", html)
