# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Connector factory. The only entry point core code may use to reach a store::

	from field_sales.connectors import get_connector

	result = get_connector().test_connection()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _

from field_sales.connectors.base import BaseConnector, ConnectorError
from field_sales.connectors.shopify import ShopifyConnector

# Built-in connectors shipped with Field Sales.
BUILTIN_CONNECTORS: dict[str, type[BaseConnector]] = {
	"Shopify": ShopifyConnector,
}


def get_connectors() -> dict[str, type[BaseConnector]]:
	"""
	Return all available connectors (built-in + discovered from hooks).

	External apps register connectors via the ``field_sales_connectors`` hook, e.g.::

		field_sales_connectors = {
			"WooCommerce": "field_sales_woocommerce.connector.WooCommerceConnector",
			"Odoo": "field_sales_odoo.connector.OdooConnector",
		}
	"""
	connectors: dict[str, type[BaseConnector]] = dict(BUILTIN_CONNECTORS)
	try:
		hooked = frappe.get_hooks("field_sales_connectors") or {}
	except Exception:
		hooked = {}

	for platform, target in hooked.items():
		if isinstance(target, (list, tuple)):
			target = target[-1] if target else None
		if not target:
			continue
		try:
			cls = frappe.get_attr(target) if isinstance(target, str) else target
			if isinstance(cls, type) and issubclass(cls, BaseConnector):
				connectors[platform] = cls
		except Exception as e:
			frappe.log_error(
				title=f"Failed to load Field Sales connector for {platform}",
				message=str(e),
			)

	return connectors


class _ConnectorRegistry(dict):
	"""Dynamic dict proxy so code accessing CONNECTORS[platform] or CONNECTORS.get(platform) works."""

	def _all(self) -> dict[str, type[BaseConnector]]:
		return get_connectors()

	def __getitem__(self, key: str) -> type[BaseConnector]:
		return self._all()[key]

	def get(self, key: str, default=None):
		return self._all().get(key, default)

	def __contains__(self, key: object) -> bool:
		return key in self._all()

	def items(self):
		return self._all().items()

	def keys(self):
		return self._all().keys()

	def values(self):
		return self._all().values()

	def __iter__(self):
		return iter(self._all())

	def __len__(self):
		return len(self._all())

	def __repr__(self):
		return repr(self._all())


CONNECTORS: dict[str, type[BaseConnector]] = _ConnectorRegistry()

__all__ = ["BaseConnector", "ConnectorError", "CONNECTORS", "get_connector", "get_connectors"]


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
