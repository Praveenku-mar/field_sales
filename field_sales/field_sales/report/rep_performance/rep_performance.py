# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Avg, Count, IfNull, Sum
from frappe.utils import flt, getdate

from field_sales.permissions import get_allowed_reps, is_admin

METRICS = (
	"planned_visits",
	"completed_visits",
	"avg_time_on_site",
	"outside_geofence",
	"orders",
	"order_value",
	"conversion",
	"failed_syncs",
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	_validate_filters(filters)

	reps = get_reps_in_scope(filters)
	data = get_rep_stats(filters.from_date, filters.to_date, reps)
	return get_columns(), data, None, get_chart(data), get_summary(data)


def _validate_filters(filters):
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"label": _("Sales Rep"), "fieldname": "sales_rep", "fieldtype": "Link", "options": "Sales Rep", "width": 200},
		{"label": _("Rep Name"), "fieldname": "sales_rep_name", "fieldtype": "Data", "width": 150},
		{"label": _("Territory"), "fieldname": "territory", "fieldtype": "Data", "width": 120},
		{"label": _("Planned Visits"), "fieldname": "planned_visits", "fieldtype": "Int", "width": 120},
		{"label": _("Completed Visits"), "fieldname": "completed_visits", "fieldtype": "Int", "width": 140},
		{"label": _("Avg Time on Site (min)"), "fieldname": "avg_time_on_site", "fieldtype": "Float", "precision": 1, "width": 170},
		{"label": _("Visits Outside Geofence"), "fieldname": "outside_geofence", "fieldtype": "Int", "width": 180},
		{"label": _("Orders"), "fieldname": "orders", "fieldtype": "Int", "width": 90},
		{"label": _("Order Value"), "fieldname": "order_value", "fieldtype": "Currency", "width": 140},
		{"label": _("Conversion %"), "fieldname": "conversion", "fieldtype": "Percent", "width": 120},
		{"label": _("Failed Syncs"), "fieldname": "failed_syncs", "fieldtype": "Int", "width": 110},
	]


def get_reps_in_scope(filters, user: str | None = None) -> dict:
	"""
	Sales Reps the user may see (own + everyone under them; all for admins),
	narrowed by the Sales Rep / Territory filters.

	:return: ``{rep: {"full_name", "territory", "active"}}``
	"""
	user = user or frappe.session.user
	rep_filters = {}
	if not is_admin(user):
		allowed = get_allowed_reps(user)
		if not allowed:
			return {}
		rep_filters["name"] = ("in", allowed)

	if filters.get("sales_rep"):
		if "name" in rep_filters and filters.sales_rep not in rep_filters["name"][1]:
			return {}
		rep_filters["name"] = filters.sales_rep

	if filters.get("territory"):
		rep_filters["territory"] = filters.territory

	return {
		r.name: r
		for r in frappe.get_all(
			"Sales Rep", filters=rep_filters, fields=["name", "full_name", "territory", "active"]
		)
	}


def get_rep_stats(from_date, to_date, reps: dict) -> list[dict]:
	"""
	One row of metrics per rep for visits and orders dated in the range.

	Inactive reps are only listed if they had activity. Also used by the
	daily manager summary, so both always agree.
	"""
	if not reps:
		return []

	visits = _visit_stats(from_date, to_date, list(reps))
	orders = _order_stats(from_date, to_date, list(reps))

	data = []
	for rep, info in reps.items():
		v = visits.get(rep, {})
		o = orders.get(rep, {})
		completed = int(v.get("completed_visits") or 0)
		order_count = int(o.get("orders") or 0)
		row = {
			"sales_rep": rep,
			"sales_rep_name": info.full_name,
			"territory": info.territory,
			"planned_visits": int(v.get("planned_visits") or 0),
			"completed_visits": completed,
			"avg_time_on_site": flt(v.get("avg_time_on_site"), 1),
			"outside_geofence": int(v.get("outside_geofence") or 0),
			"orders": order_count,
			"order_value": flt(o.get("order_value"), 2),
			# Orders can be taken without a visit, so this can exceed 100%.
			"conversion": flt(order_count * 100.0 / completed, 2) if completed else 0.0,
			"failed_syncs": int(o.get("failed_syncs") or 0),
		}
		if info.active or any(row[m] for m in METRICS):
			data.append(row)

	return sorted(data, key=lambda d: (-d["order_value"], d["sales_rep"]))


def _visit_stats(from_date, to_date, reps: list[str]) -> dict:
	visit = frappe.qb.DocType("Visit")
	case = frappe.qb.terms.Case
	is_completed = visit.status == "Completed"
	outside = visit.check_in_time.isnotnull() & (visit.within_geofence == 0)

	rows = (
		frappe.qb.from_(visit)
		.select(
			visit.sales_rep,
			Count("*").as_("planned_visits"),
			Sum(case().when(is_completed, 1).else_(0)).as_("completed_visits"),
			Avg(case().when(is_completed, visit.time_on_site_min)).as_("avg_time_on_site"),
			Sum(case().when(outside, 1).else_(0)).as_("outside_geofence"),
		)
		.where(visit.visit_date.between(from_date, to_date))
		.where(visit.status != "Cancelled")
		.where(visit.sales_rep.isin(reps))
		.groupby(visit.sales_rep)
	).run(as_dict=True)
	return {r.sales_rep: r for r in rows}


def _order_stats(from_date, to_date, reps: list[str]) -> dict:
	order = frappe.qb.DocType("Field Order")
	failed = frappe.qb.terms.Case().when(order.sync_status == "Failed", 1).else_(0)

	rows = (
		frappe.qb.from_(order)
		.select(
			order.sales_rep,
			Count("*").as_("orders"),
			Sum(IfNull(order.grand_total, 0)).as_("order_value"),
			Sum(failed).as_("failed_syncs"),
		)
		.where(order.docstatus == 1)
		.where(order.order_date.between(from_date, to_date))
		.where(order.sales_rep.isin(reps))
		.groupby(order.sales_rep)
	).run(as_dict=True)
	return {r.sales_rep: r for r in rows}


def get_chart(data: list[dict]) -> dict | None:
	rows = [d for d in data if d["order_value"]]
	if not rows:
		return None
	return {
		"data": {
			"labels": [d["sales_rep_name"] or d["sales_rep"] for d in rows],
			"datasets": [{"name": _("Order Value"), "values": [d["order_value"] for d in rows]}],
		},
		"type": "bar",
		"fieldtype": "Currency",
		"colors": ["#2490ef"],
	}


def get_summary(data: list[dict]) -> list[dict]:
	total_visits = sum(d["planned_visits"] for d in data)
	completed = sum(d["completed_visits"] for d in data)
	total_orders = sum(d["orders"] for d in data)
	total_value = sum(d["order_value"] for d in data)
	# Weighted by visits: total orders over total completed visits, not a mean of rep percentages.
	conversion = flt(total_orders * 100.0 / completed, 2) if completed else 0.0

	return [
		{"label": _("Total Visits"), "value": total_visits, "datatype": "Int", "indicator": "Blue"},
		{"label": _("Total Orders"), "value": total_orders, "datatype": "Int", "indicator": "Blue"},
		{"label": _("Total Value"), "value": total_value, "datatype": "Currency", "indicator": "Green"},
		{
			"label": _("Avg Conversion"),
			"value": conversion,
			"datatype": "Percent",
			"indicator": "Green" if conversion >= 50 else "Orange",
		},
	]
