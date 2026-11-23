# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Field Orders and Platform Products now record their currency (Phase 5).

Existing rows get their store's currency (``utils/money.py -> store_currency``): the shop's on the
shared multi-store site, else the site's default, which every amount was in until now. Updated
in batches, so a large table is never locked in one statement. Safe to run again.
"""

import frappe

from field_sales.utils.money import store_currency

DOCTYPES = ("Field Order", "Platform Product")
BATCH = 10000


def execute():
	for doctype in DOCTYPES:
		frappe.reload_doc("field_sales", "doctype", frappe.scrub(doctype))
	for store in frappe.get_all("Store", pluck="name"):
		currency = store_currency(store)
		for doctype in DOCTYPES:
			_fill(doctype, {"store": store}, currency)
	# Rows without a store (none after create_first_store): the site's default.
	for doctype in DOCTYPES:
		_fill(doctype, None, store_currency())


def _fill(doctype: str, where: dict | None, currency: str) -> None:
	condition, values = "(currency is null or currency = '')", {"currency": currency, "batch": BATCH}
	if where:
		condition += " and store = %(store)s"
		values["store"] = where["store"]
	else:
		condition += " and (store is null or store = '')"
	while True:
		frappe.db.sql(
			f"update `tab{doctype}` set currency = %(currency)s where {condition} limit %(batch)s",
			values,
		)
		changed = frappe.db.sql("select row_count()")[0][0]
		frappe.db.commit()
		if not changed:
			break
