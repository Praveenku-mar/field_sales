# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Count, IfNull, Sum
from frappe.utils import flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	_validate_filters(filters)
	return get_columns(), get_data(filters)


def _validate_filters(filters):
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"label": _("Sales Rep"), "fieldname": "sales_rep", "fieldtype": "Link", "options": "Sales Rep", "width": 200},
		{"label": _("Rep Name"), "fieldname": "sales_rep_name", "fieldtype": "Data", "width": 160},
		{"label": _("Visits"), "fieldname": "visits", "fieldtype": "Int", "width": 100},
		{"label": _("Completed Visits"), "fieldname": "completed_visits", "fieldtype": "Int", "width": 140},
		{"label": _("Orders"), "fieldname": "orders", "fieldtype": "Int", "width": 100},
		{"label": _("Order Value"), "fieldname": "order_value", "fieldtype": "Currency", "width": 140},
		{"label": _("Conversion %"), "fieldname": "conversion", "fieldtype": "Percent", "width": 120},
	]


def get_data(filters):
	visits = _get_visit_stats(filters)
	orders = _get_order_stats(filters)

	reps = set(visits) | set(orders)
	if not reps:
		return []

	rep_names = dict(
		frappe.get_all("Sales Rep", filters={"name": ("in", list(reps))}, fields=["name", "full_name"], as_list=True)
	)

	data = []
	for rep in sorted(reps):
		v = visits.get(rep, {})
		o = orders.get(rep, {})
		completed = v.get("completed_visits", 0)
		order_count = o.get("orders", 0)
		data.append(
			{
				"sales_rep": rep,
				"sales_rep_name": rep_names.get(rep),
				"visits": v.get("visits", 0),
				"completed_visits": completed,
				"orders": order_count,
				"order_value": flt(o.get("order_value")),
				# Orders can be taken without a visit, so this can exceed 100%.
				"conversion": flt(order_count * 100.0 / completed, 2) if completed else 0.0,
			}
		)

	return sorted(data, key=lambda d: d["order_value"], reverse=True)


def _get_visit_stats(filters):
	visit = frappe.qb.DocType("Visit")
	completed = frappe.qb.terms.Case().when(visit.status == "Completed", 1).else_(0)

	query = (
		frappe.qb.from_(visit)
		.select(visit.sales_rep, Count("*").as_("visits"), Sum(completed).as_("completed_visits"))
		.where(visit.visit_date.between(filters.from_date, filters.to_date))
		.where(visit.status != "Cancelled")
		.where(IfNull(visit.sales_rep, "") != "")
		.groupby(visit.sales_rep)
	)
	if filters.sales_rep:
		query = query.where(visit.sales_rep == filters.sales_rep)

	return {r.sales_rep: r for r in query.run(as_dict=True)}


def _get_order_stats(filters):
	order = frappe.qb.DocType("Field Order")

	query = (
		frappe.qb.from_(order)
		.select(order.sales_rep, Count("*").as_("orders"), Sum(order.grand_total).as_("order_value"))
		.where(order.docstatus == 1)
		.where(order.order_date.between(filters.from_date, filters.to_date))
		.where(IfNull(order.sales_rep, "") != "")
		.groupby(order.sales_rep)
	)
	if filters.sales_rep:
		query = query.where(order.sales_rep == filters.sales_rep)

	return {r.sales_rep: r for r in query.run(as_dict=True)}
