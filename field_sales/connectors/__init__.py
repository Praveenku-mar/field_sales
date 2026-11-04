# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Connector factory. The entry point core code uses to reach a store::

	from field_sales.connectors import get_connector

	result = get_connector().test_connection()
"""

from __future__ import annotations

import frappe
from frappe import _

from field_sales.connectors.base import BaseConnector, ConnectorError
from field_sales.connectors.shopify import ShopifyConnector

CONNECTORS: dict[str, type[BaseConnector]] = {
	"Shopify": ShopifyConnector,
}

__all__ = ["BaseConnector", "ConnectorError", "CONNECTORS", "get_connector"]


def get_connector(require_enabled: bool = True) -> ShopifyConnector:
	"""
	Return the Shopify connector configured on Store Connection.

	Raises a ``frappe.ValidationError`` if the connection is disabled or the
	platform is not Shopify.
	"""
	settings = frappe.get_single("Store Connection")
	if require_enabled and not settings.enabled:
		frappe.throw(_("Store Connection is disabled"))

	connector_cls = CONNECTORS.get(settings.platform)
	if not connector_cls:
		frappe.throw(_("Platform {0} is not supported").format(settings.platform or _("(not set)")))

	return connector_cls(settings)
