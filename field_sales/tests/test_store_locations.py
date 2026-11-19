# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""The Settings page's location list reads every page of the store's locations, not the first 20."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from field_sales.connectors import get_connector
from field_sales.connectors.shopify import LOCATIONS_QUERY, ShopifyConnector


def node(location_id, name, active=True):
	return {"id": f"gid://shopify/Location/{location_id}", "name": name, "isActive": active}


class TestStoreLocations(IntegrationTestCase):
	def setUp(self):
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": "Shopify", "enabled": 1, "store_url": "test-shop.myshopify.com", "shopify_api_version": "2026-10"},
		)

	def tearDown(self):
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	def test_every_page_is_read(self):
		pages = {
			None: {
				"nodes": [node(1, "Shop location"), node(2, "Closed shop", active=False)],
				"pageInfo": {"hasNextPage": True, "endCursor": "page-2"},
			},
			"page-2": {
				"nodes": [node(21, "Coimbatore"), node(22, "Madurai")],
				"pageInfo": {"hasNextPage": False, "endCursor": "page-2-end"},
			},
		}
		asked = []

		def graphql(self_, query, variables=None, **kwargs):
			self.assertEqual(query, LOCATIONS_QUERY)
			asked.append(variables["cursor"])
			return {"locations": pages[variables["cursor"]]}

		with patch.object(ShopifyConnector, "_graphql", graphql):
			locations = get_connector().get_locations()

		self.assertEqual(asked, [None, "page-2"])
		self.assertEqual(
			locations,
			[
				{"id": "1", "name": "Shop location"},
				{"id": "21", "name": "Coimbatore"},
				{"id": "22", "name": "Madurai"},
			],
		)

	def test_a_cursor_that_never_ends_stops(self):
		def graphql(self_, query, variables=None, **kwargs):
			return {"locations": {"nodes": [node(1, "Loop")], "pageInfo": {"hasNextPage": True, "endCursor": "again"}}}

		with patch.object(ShopifyConnector, "_graphql", graphql) as _patched:
			locations = get_connector().get_locations()
		self.assertLessEqual(len(locations), 40)
