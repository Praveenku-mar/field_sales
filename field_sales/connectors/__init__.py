# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Connector factory. The only entry point core code may use to reach a store::

	from field_sales.connectors import get_connector

	result = get_connector().test_connection()
"""

import frappe
from frappe import _

from field_sales.connectors.base import BaseConnector, ConnectorError
from field_sales.connectors.shopify import ShopifyConnector

# WooCommerce / Odoo connectors register here when they are added.
CONNECTORS: dict[str, type[BaseConnector]] = {
	"Shopify": ShopifyConnector,
}

__all__ = ["BaseConnector", "ConnectorError", "get_connector"]


def get_connector(require_enabled: bool = True) -> BaseConnector:
	"""
	Return a connector for the platform configured on Store Connection.

	Raises a ``frappe.ValidationError`` if the connection is disabled or the
	platform has no connector yet.

	:param require_enabled: Pass ``False`` to skip the disabled check, for callers
		(webhook receiver) that decide for themselves what a disabled connection means.
	"""
	settings = frappe.get_single("Store Connection")
	if require_enabled and not settings.enabled:
		frappe.throw(_("Store Connection is disabled"))

	connector_cls = CONNECTORS.get(settings.platform)
	if not connector_cls:
		frappe.throw(_("Platform {0} is not supported yet").format(settings.platform or _("(not set)")))

	return connector_cls(settings)
