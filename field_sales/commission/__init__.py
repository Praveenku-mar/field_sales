# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint

from field_sales.utils.store import get_store_value


def commission_enabled() -> bool:
	"""
	Whether this store pays commission (Store → Pay Commission to Sales Reps).

	A site with no Store yet counts as on (the field's default).
	"""
	value = get_store_value("enable_commission")
	return value is None or bool(cint(value))


def require_commission_enabled() -> None:
	"""Refuse new commission work while commission is turned off."""
	if not commission_enabled():
		frappe.throw(
			_("Commission is turned off for this company. Turn it on in Settings → Commission."),
			title=_("Commission is off"),
		)
