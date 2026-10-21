# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.connectors import get_connector
from field_sales.connectors.base import ConnectorError
from field_sales.connectors.shopify import (
	METAFIELD_NAMESPACE,
	METAFIELD_OWNER_TYPES,
	ORDER_METAFIELDS,
	ShopifyConnector,
	_order_metafields,
)

SHOP = "test-shop.myshopify.com"


class TestOrderMetafields(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(
			"Store Connection",
			{
				"platform": "Shopify",
				"enabled": 1,
				"store_url": SHOP,
				"shopify_api_version": "2026-10",
				"order_mode": "Confirmed",
			},
		)
		# Not set up yet (an empty Datetime single would read back as 0001-01-01).
		frappe.db.delete("Singles", {"doctype": "Store Connection", "field": "metafields_ready_on"})
		self.conn = get_connector()

		user = frappe.get_doc(
			{"doctype": "User", "email": "meta.rep@example.com", "first_name": "Meta", "last_name": "Rep", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		self.rep = frappe.get_doc({"doctype": "Sales Rep", "user": user.name, "full_name": "Meta Rep"}).insert(
			ignore_permissions=True
		)
		self.order = frappe._dict(name="FO-META-1", sales_rep=self.rep.name)

	def tearDown(self):
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def values(self, metafields):
		return {m["key"]: m["value"] for m in metafields}

	def test_values_are_order_no_and_rep_name_with_email(self):
		metafields = _order_metafields(self.order)
		self.assertEqual({m["namespace"] for m in metafields}, {METAFIELD_NAMESPACE})
		self.assertEqual({m["type"] for m in metafields}, {"single_line_text_field"})
		self.assertEqual(
			self.values(metafields),
			{"field_order_no": "FO-META-1", "sales_rep": "Meta Rep <meta.rep@example.com>"},
		)

	def test_ensure_creates_every_definition_and_treats_taken_as_done(self):
		calls = []

		def graphql(query, variables=None, **kwargs):
			calls.append(variables["definition"])
			if len(calls) == 1:
				raise ConnectorError("already exists", code="USER_ERROR", details=[{"code": "TAKEN"}])
			return {"metafieldDefinitionCreate": {"createdDefinition": {"id": "gid://x"}, "userErrors": []}}

		with patch.object(ShopifyConnector, "_graphql", side_effect=graphql):
			self.assertTrue(self.conn.ensure_order_metafields())

		self.assertEqual(len(calls), len(METAFIELD_OWNER_TYPES) * len(ORDER_METAFIELDS))
		self.assertEqual({(c["ownerType"], c["key"]) for c in calls}, {(o, k) for o in METAFIELD_OWNER_TYPES for k in ORDER_METAFIELDS})
		self.assertTrue(all(c["access"] == {"admin": "MERCHANT_READ"} for c in calls))
		self.assertTrue(frappe.get_single("Store Connection").metafields_ready_on)

	def test_ensure_failure_is_reported_and_not_marked_done(self):
		error = ConnectorError("bad", code="USER_ERROR", details=[{"code": "INVALID"}])
		with patch.object(ShopifyConnector, "_graphql", side_effect=error):
			self.assertFalse(self.conn.ensure_order_metafields())
		self.assertFalse(frappe.get_single("Store Connection").metafields_ready_on)

	def test_find_order_uses_source_identifier_then_the_old_tag(self):
		queries = []

		def graphql(query, variables=None, **kwargs):
			queries.append(variables["q"])
			nodes = [{"id": "gid://shopify/Order/5", "name": "#1005"}] if len(queries) == 2 else []
			return {"orders": {"nodes": nodes}}

		with patch.object(ShopifyConnector, "_graphql", side_effect=graphql):
			found = self.conn._find_order(self.order)

		self.assertEqual(queries, ['source_identifier:"FO-META-1"', 'tag:"field-order-FO-META-1"'])
		self.assertEqual(found["name"], "#1005")

	def test_order_inputs_carry_metafields_and_one_tag(self):
		retailer = frappe._dict(name="RET-X", external_customer_id="77", phone="9876543210")
		# SimpleNamespace, not frappe._dict: a dict's .items method would hide the rows.
		order = SimpleNamespace(
			name="FO-META-1",
			sales_rep=self.rep.name,
			location_name=None,
			items=[frappe._dict(idx=1, external_variant_id="501", qty=2, rate=10, product="P", product_title="P")],
			tax_amount=0,
			tax_percent=0,
			discount_amount=0,
			location_id=None,
		)
		sent = {}

		def graphql(query, variables=None, **kwargs):
			if "currencyCode" in query and "shop" in query:
				return {"shop": {"currencyCode": "INR"}}
			if "orderCreate" in query:
				sent["order"] = variables["order"]
				return {"orderCreate": {"order": {"id": "gid://shopify/Order/9", "name": "#9"}}}
			sent["draft"] = variables["input"]
			return {"draftOrderCreate": {"draftOrder": {"id": "gid://shopify/DraftOrder/3", "status": "OPEN", "order": None}}}

		with patch.object(ShopifyConnector, "_graphql", side_effect=graphql), patch(
			"field_sales.connectors.shopify._customer_address", return_value={}
		):
			self.conn._create_confirmed_order(order, retailer)
			self.conn._create_draft(order, retailer)

		for key in ("order", "draft"):
			self.assertEqual(sent[key]["tags"], ["field-sales"])
			self.assertEqual(self.values(sent[key]["metafields"])["field_order_no"], "FO-META-1")

	def test_set_order_metafields_targets_the_order(self):
		with patch.object(ShopifyConnector, "_graphql", return_value={}) as graphql:
			self.conn.set_order_metafields("123", self.order)
		metafields = graphql.call_args.args[1]["metafields"]
		self.assertEqual({m["ownerId"] for m in metafields}, {"gid://shopify/Order/123"})
		self.assertEqual(self.values(metafields)["sales_rep"], "Meta Rep <meta.rep@example.com>")
