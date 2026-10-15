# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Whitelisted commission endpoints for the app's screens.

A rep sees their own numbers; a manager sees the team's (own rep plus all reports below, see
``field_sales.permissions``). Entry lists go through ``frappe.get_list``, so row permissions apply.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate, nowdate

from field_sales.permissions import get_allowed_reps, get_own_rep, is_admin

PERIODS = ("this_month", "last_month", "this_year", "all")
MAX_PAGE_LENGTH = 100


@frappe.whitelist()
def get_my_commission_summary(period: str = "this_month") -> dict:
	"""
	The signed-in rep's commission for ``period`` ("this_month", "last_month", "this_year" or "all").

	:return: ``{"currency", "earned", "pending", "reversed", "net", "next_slab_gap", "top_products"}``.
		``earned`` is before reversals; ``next_slab_gap`` is ``{"amount", "rate"}`` for a Tiered Slab
		plan (sales still needed this month to reach the next slab) or ``None``;
		``top_products`` are the 5 products with the most commission.
	"""
	rep = get_own_rep()
	if not rep:
		frappe.throw(_("Your user is not linked to a Sales Rep."), frappe.PermissionError)

	start, end = _period_range(period)
	totals = _totals([rep], start, end).get(rep, _empty_totals())
	return {
		"currency": frappe.db.get_default("currency"),
		"earned": totals["earned"],
		"pending": totals["pending"],
		"reversed": totals["reversed"],
		"net": totals["net"],
		"next_slab_gap": _next_slab_gap(rep),
		"top_products": _top_products(rep, start, end),
	}


@frappe.whitelist()
def get_team_commission(period: str = "this_month") -> list[dict]:
	"""
	Commission totals per rep for ``period``, for the signed-in manager's team (everyone for admins).

	:return: ``[{"sales_rep", "full_name", "earned", "pending", "reversed", "override", "net"}, ...]``
		sorted by net, highest first.
	"""
	if is_admin():
		reps = frappe.get_all("Sales Rep", pluck="name")
	else:
		reps = get_allowed_reps()
	if not reps:
		return []

	start, end = _period_range(period)
	totals = _totals(reps, start, end)
	names = {r.name: r.full_name for r in frappe.get_all("Sales Rep", filters={"name": ("in", reps)}, fields=["name", "full_name"])}
	rows = [{"sales_rep": rep, "full_name": names.get(rep), **totals.get(rep, _empty_totals())} for rep in reps]
	return sorted(rows, key=lambda r: r["net"], reverse=True)


@frappe.whitelist()
def get_commission_entries(filters: dict | str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""
	One page of Commission Entries the user may see, newest first.

	:param filters: Standard filters, as a dict or JSON string (e.g. ``{"status": "Earned"}``).
	:param page: 1-based page number.
	:param page_length: Rows per page (at most 100).
	:return: ``{"entries": [...], "page", "page_length", "total"}``
	"""
	if isinstance(filters, str):
		filters = json.loads(filters or "{}")
	page = max(1, int(page))
	page_length = min(MAX_PAGE_LENGTH, max(1, int(page_length)))

	total = frappe.get_list("Commission Entry", filters=filters or {}, fields=[{"COUNT": "*", "as": "total"}])
	entries = frappe.get_list(
		"Commission Entry",
		filters=filters or {},
		fields=[
			"name",
			"entry_type",
			"beneficiary_type",
			"sales_rep",
			"source_rep",
			"status",
			"posting_date",
			"earned_on",
			"platform_order",
			"product",
			"base_amount",
			"effective_rate",
			"amount",
			"reversed_amount",
			"currency",
			"commission_statement",
			"calculation_note",
		],
		order_by="posting_date desc, creation desc",
		start=(page - 1) * page_length,
		page_length=page_length,
	)
	return {"entries": entries, "page": page, "page_length": page_length, "total": total[0].total if total else 0}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _period_range(period: str):
	"""(start, end) dates of a named period; ``None`` bounds mean unbounded."""
	if period not in PERIODS:
		frappe.throw(_("Period must be one of: {0}").format(", ".join(PERIODS)))

	today = getdate(nowdate())
	if period == "this_month":
		return get_first_day(today), get_last_day(today)
	if period == "last_month":
		last = add_months(today, -1)
		return get_first_day(last), get_last_day(last)
	if period == "this_year":
		return today.replace(month=1, day=1), today.replace(month=12, day=31)
	return None, None


def _empty_totals() -> dict:
	return {"earned": 0.0, "pending": 0.0, "reversed": 0.0, "override": 0.0, "net": 0.0}


def _date_filter(start, end):
	return ("between", (start, end)) if start and end else None


def _totals(reps: list[str], start, end) -> dict[str, dict]:
	"""
	Per rep: earned (Earned / Reversed entries by earned date, plus Adjustments by posting date, before
	reversals), pending (Pending, net of reversals), reversed (reversals and negative Adjustments), override
	(the Manager Override part of earned, net of reversals) and net.
	"""
	totals = {rep: _empty_totals() for rep in reps}
	rows = frappe.get_all(
		"Commission Entry",
		filters={"sales_rep": ("in", reps), "status": ("in", ("Pending", "Earned", "Reversed"))},
		fields=[
			"sales_rep",
			"entry_type",
			"beneficiary_type",
			"status",
			"amount",
			"reversed_amount",
			"posting_date",
			"earned_on",
		],
	)
	for row in rows:
		t = totals[row.sales_rep]
		amount, reversed_amount = flt(row.amount), flt(row.reversed_amount)

		if row.status == "Pending":
			if _in_range(row.posting_date, start, end):
				t["pending"] += amount - reversed_amount
		elif row.entry_type == "Adjustment":
			if _in_range(row.posting_date, start, end):
				if amount < 0:
					t["reversed"] += -amount
				else:
					t["earned"] += amount
				if row.beneficiary_type == "Manager Override":
					t["override"] += amount
		elif _in_range(row.earned_on, start, end):
			t["earned"] += amount
			t["reversed"] += reversed_amount
			if row.beneficiary_type == "Manager Override":
				t["override"] += amount - reversed_amount

	for t in totals.values():
		t["net"] = t["earned"] - t["reversed"]
		for key in t:
			t[key] = flt(t[key], 2)
	return totals


def _in_range(value, start, end) -> bool:
	if not start or not end:
		return True
	return bool(value) and start <= getdate(value) <= end


def _top_products(rep: str, start, end) -> list[dict]:
	filters = {
		"sales_rep": rep,
		"entry_type": "Order",
		"status": ("in", ("Pending", "Earned")),
		"product": ("is", "set"),
	}
	window = _date_filter(start, end)
	if window:
		filters["posting_date"] = window

	per_product: dict[str, float] = {}
	for row in frappe.get_all("Commission Entry", filters=filters, fields=["product", "amount", "reversed_amount"]):
		per_product[row.product] = per_product.get(row.product, 0) + flt(row.amount) - flt(row.reversed_amount)

	top = sorted(per_product.items(), key=lambda kv: kv[1], reverse=True)[:5]
	return [
		{"product": p, "title": frappe.db.get_value("Platform Product", p, "product_title"), "commission": flt(c, 2)}
		for p, c in top
	]


def _next_slab_gap(rep: str) -> dict | None:
	"""Sales still needed this month to reach the next slab of a Tiered Slab plan, with that slab's rate."""
	plan_name = frappe.db.get_value("Sales Rep", rep, "commission_plan")
	if not plan_name or frappe.db.get_value("Commission Plan", plan_name, "rule_type") != "Tiered Slab":
		return None

	today = getdate(nowdate())
	rows = frappe.get_all(
		"Commission Entry",
		filters={
			"sales_rep": rep,
			"entry_type": "Order",
			"beneficiary_type": "Rep",
			"status": ("in", ("Pending", "Earned")),
			"posting_date": ("between", (get_first_day(today), get_last_day(today))),
		},
		fields=[{"SUM": "base_amount", "as": "total"}],
	)
	sales = flt(rows[0].total) if rows else 0

	plan = frappe.get_doc("Commission Plan", plan_name)
	for slab in sorted(plan.slabs, key=lambda s: flt(s.from_amount)):
		if flt(slab.from_amount) > sales:
			return {"amount": flt(flt(slab.from_amount) - sales, 2), "rate": flt(slab.rate)}
	return None
