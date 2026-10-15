# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Row-level access by Sales Rep hierarchy.

A user sees records of their own Sales Rep and of every rep reporting to it,
directly or indirectly (Sales Rep.reports_to). System Manager and
Administrator see everything; a user with no Sales Rep sees nothing.

Retailers are the exception: every rep can see every retailer (to plan a
visit or take an order anywhere), but can edit only those of their hierarchy.
Lists of retailers needing the rep's action use ``hierarchy_filters``.

Wired up in hooks.py through ``permission_query_conditions`` (list views,
reports, link search) and ``has_permission`` (opening / saving one record).
"""

from collections import defaultdict, deque

import frappe

ADMIN_ROLES = {"System Manager"}

# Permission types that only look at a record (any retailer may be seen).
READ_ONLY_PTYPES = {"read", "select", "print", "email", "report", "export"}

# DocType -> field holding the Sales Rep the record belongs to.
REP_FIELD = {
	"Visit": "sales_rep",
	"Field Order": "sales_rep",
	"Retailer": "sales_rep",
	"Commission Entry": "sales_rep",
	"Commission Statement": "sales_rep",
	"Sales Rep": "name",
}


def is_admin(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(ADMIN_ROLES & set(frappe.get_roles(user)))


def get_own_rep(user: str | None = None) -> str | None:
	"""The Sales Rep linked to this user (Sales Rep.user is unique)."""
	return frappe.db.get_value("Sales Rep", {"user": user or frappe.session.user}, "name")


def get_allowed_reps(user: str | None = None) -> list[str]:
	"""
	The user's own Sales Rep followed by every rep under it, at all levels.

	Walks reports_to breadth-first with a visited set, so a cycle in the
	hierarchy (A → B → A) cannot loop. Cached for the rest of the request.
	"""
	user = user or frappe.session.user
	cache = getattr(frappe.local, "field_sales_allowed_reps", None)
	if cache is None:
		cache = frappe.local.field_sales_allowed_reps = {}
	if user in cache:
		return cache[user]

	allowed = []
	own_rep = get_own_rep(user)
	if own_rep:
		reports = defaultdict(list)
		# One query for the whole hierarchy; frappe.get_all bypasses these permission hooks.
		for rep in frappe.get_all("Sales Rep", fields=["name", "reports_to"], filters={"reports_to": ("is", "set")}):
			reports[rep.reports_to].append(rep.name)

		seen = {own_rep}
		queue = deque([own_rep])
		while queue:
			rep = queue.popleft()
			allowed.append(rep)
			for child in reports[rep]:
				if child not in seen:
					seen.add(child)
					queue.append(child)

	cache[user] = allowed
	return allowed


def clear_allowed_reps_cache() -> None:
	frappe.local.field_sales_allowed_reps = {}


# ----------------------------------------------------------------------
# permission_query_conditions
# ----------------------------------------------------------------------


def _query_conditions(doctype: str, user: str | None) -> str:
	user = user or frappe.session.user
	if is_admin(user):
		return ""

	reps = get_allowed_reps(user)
	if not reps:
		return "1=0"

	values = ", ".join(frappe.db.escape(rep) for rep in reps)
	return f"`tab{doctype}`.`{REP_FIELD[doctype]}` in ({values})"


def visit_query(user: str | None = None) -> str:
	return _query_conditions("Visit", user)


def sales_rep_query(user: str | None = None) -> str:
	return _query_conditions("Sales Rep", user)


def field_order_query(user: str | None = None) -> str:
	return _query_conditions("Field Order", user)


def commission_entry_query(user: str | None = None) -> str:
	return _query_conditions("Commission Entry", user)


def commission_statement_query(user: str | None = None) -> str:
	return _query_conditions("Commission Statement", user)


def retailer_query(user: str | None = None) -> str:
	# Every retailer is listed; role permissions still decide who may read at all.
	return ""


def hierarchy_filters(doctype: str, user: str | None = None) -> list:
	"""
	Filters limiting a list to the user's hierarchy, for doctypes whose read
	access is wider (Retailer): e.g. "my retailers that failed to sync".
	"""
	user = user or frappe.session.user
	if is_admin(user):
		return []
	return [[REP_FIELD[doctype], "in", get_allowed_reps(user) or [""]]]


# ----------------------------------------------------------------------
# has_permission
#
# Frappe treats any falsy return (including None) as "denied", so every
# path returns an explicit bool. These hooks can only deny; role
# permissions still decide whether the user may read / write at all.
# ----------------------------------------------------------------------


def visit_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _rep_in_allowed(doc.sales_rep, user)


def field_order_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _rep_in_allowed(doc.sales_rep, user)


def commission_entry_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _rep_in_allowed(doc.sales_rep, user)


def commission_statement_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _rep_in_allowed(doc.sales_rep, user)


def retailer_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	# sales_rep is optional on Retailer. A rep may create one without it;
	# Retailer.before_insert then assigns it to them.
	if ptype == "create" and not doc.sales_rep:
		return is_admin(user) or bool(get_allowed_reps(user))
	if ptype in READ_ONLY_PTYPES:
		return True
	return _rep_in_allowed(doc.sales_rep, user)


def sales_rep_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	# A new Sales Rep has no name yet when create is checked: a manager may
	# add reps under themselves or anyone below them.
	if ptype == "create" and doc.is_new():
		return _rep_in_allowed(doc.reports_to, user)
	return _rep_in_allowed(doc.name, user)


def _rep_in_allowed(rep: str | None, user: str | None) -> bool:
	if is_admin(user):
		return True
	return bool(rep) and rep in get_allowed_reps(user)
