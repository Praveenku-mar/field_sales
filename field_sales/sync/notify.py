# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Realtime "sync finished" events for open forms and the Sync Failures page, and sync failure notifications."""

import frappe
from frappe import _

from field_sales.permissions import ADMIN_ROLES, MANAGER_ROLES
from field_sales.push.notify import notify_users
from field_sales.utils.store import get_store_name, is_multi_store, user_store

# Roles that can open the Sync Failures page and inspect sync logs (Admin only).
MONITOR_ROLES = tuple(ADMIN_ROLES)


def notify_sync_result(event: str, doctype: str, name: str, sync_status: str | None) -> None:
	"""
	Publish ``event`` with ``{"doctype", "name", "sync_status"}`` once the
	current transaction commits.

	Sent to the document's room (its open form) and to each user who can open
	the Sync Failures page, rather than to everyone: reps should not receive
	events about other reps' records.
	"""
	message = {"doctype": doctype, "name": name, "sync_status": sync_status}
	frappe.publish_realtime(event, message, doctype=doctype, docname=name, after_commit=True)
	for user in _monitor_users(frappe.db.get_value(doctype, name, "store")):
		frappe.publish_realtime(event, message, user=user, after_commit=True)


def _monitor_users(store: str | None = None) -> list[str]:
	"""Enabled users of the store who can open the Sync Failures page."""
	return users_with_roles(MONITOR_ROLES, store)


def users_with_roles(roles: tuple[str, ...], store: str | None = None, vendor: bool = False) -> list[str]:
	"""
	Enabled users with any of ``roles`` **in one store**; cached for the rest of the request / job.

	:param store: The store whose users to list (default: the current store). A user belongs to
		the store of their User Permission; on a single-store site a user with none belongs to
		its one store, on the shared site to none.
	:param vendor: Instead, the vendor staff holding the roles (users of no store).
	"""
	if not vendor:
		store = get_store_name(store)
	cache = getattr(frappe.local, "field_sales_role_users", None)
	if cache is None:
		cache = frappe.local.field_sales_role_users = {}
	key = (tuple(sorted(roles)), store, vendor)
	if key not in cache:
		has_role = frappe.qb.DocType("Has Role")
		user = frappe.qb.DocType("User")
		cache[key] = (
			frappe.qb.from_(has_role)
			.join(user)
			.on(user.name == has_role.parent)
			.select(has_role.parent)
			.distinct()
			.where(has_role.parenttype == "User")
			.where(has_role.role.isin(tuple(sorted(roles))))
			.where(user.enabled == 1)
			.where(user.name != "Guest")
		).run(pluck=True)
		cache[key] = [u for u in cache[key] if _in_scope(u, store, vendor)]
	return cache[key]


def _in_scope(user: str, store: str | None, vendor: bool) -> bool:
	own = user_store(user)
	if vendor:
		return not own
	return own == store or (not own and not is_multi_store())


def clear_role_users_cache() -> None:
	frappe.local.field_sales_role_users = {}


def get_manager_user_for_rep(sales_rep: str) -> str | None:
	"""
	Find the Sales Manager user responsible for ``sales_rep`` via reports_to.

	Traverses up the reports_to hierarchy looking for an enabled user with the
	Sales Manager role. Falls back to the nearest enabled manager in the chain.
	"""
	if not sales_rep:
		return None
	current = frappe.db.get_value("Sales Rep", sales_rep, "reports_to")
	seen = {sales_rep}
	first_manager = None
	while current and current not in seen:
		seen.add(current)
		user = frappe.db.get_value("Sales Rep", current, "user") or current
		if frappe.db.get_value("User", user, "enabled"):
			if not first_manager:
				first_manager = user
			roles = set(frappe.get_roles(user))
			if MANAGER_ROLES & roles:
				return user
		current = frappe.db.get_value("Sales Rep", current, "reports_to")
	return first_manager


def get_system_admin_recipients(store: str | None = None) -> list[str]:
	"""Return user emails for Administrator and the store's enabled admins (System Manager / Store Admin)."""
	admin_users = set(users_with_roles(ADMIN_ROLES, store))
	if frappe.db.get_value("User", "Administrator", "enabled"):
		admin_users.add("Administrator")
	recipients = []
	for user in sorted(admin_users):
		email = frappe.db.get_value("User", user, "email") or user
		if email:
			recipients.append(email)
	return recipients


def notify_order_sync_failure(doc) -> None:
	"""
	Send a notification to the Sales Manager responsible for the Sales Representative
	who created the order.
	"""
	sales_rep = doc.sales_rep or frappe.db.get_value("Sales Rep", {"user": doc.owner}, "name")
	manager_user = get_manager_user_for_rep(sales_rep)
	if not manager_user:
		return

	recipient = frappe.db.get_value("User", manager_user, "email") or manager_user
	subject = _("Field Order {0} failed to sync").format(doc.name)
	order_url = frappe.utils.get_url_to_form("Field Order", doc.name)
	retailer_label = frappe.utils.escape_html(doc.retailer_name or doc.retailer or "")
	rep_label = frappe.utils.escape_html(doc.sales_rep or "")
	error_label = frappe.utils.escape_html(doc.sync_error or "")
	attempts = doc.sync_attempts or 1
	email_content = _(
		'<p>Field Order <a href="{0}">{1}</a> for {2} (Sales Rep: {3}) failed to sync to the store (attempt {4}).</p><p><b>Error:</b> {5}</p>'
	).format(
		order_url,
		frappe.utils.escape_html(doc.name),
		retailer_label,
		rep_label,
		attempts,
		error_label,
	)
	notify_users(
		[recipient],
		"order_sync_failed",
		"Field Order",
		doc.name,
		subject,
		email_content=email_content,
		from_user=doc.owner or "Administrator",
		dedupe_on=["document_type", "document_name", "subject"],
	)


def notify_customer_sync_failure(doc) -> None:
	"""
	Send a notification to the System Administrator when a customer sync fails.
	"""
	recipients = get_system_admin_recipients(doc.get("store"))
	if not recipients:
		return

	customer_label = doc.retailer_name or doc.name
	subject = _("Customer {0} failed to sync").format(customer_label)
	customer_url = frappe.utils.get_url_to_form("Retailer", doc.name)
	error_label = frappe.utils.escape_html(doc.sync_error or "")
	email_content = _(
		'<p>Customer <a href="{0}">{1}</a> failed to sync to the store.</p><p><b>Error:</b> {2}</p>'
	).format(customer_url, frappe.utils.escape_html(customer_label), error_label)
	notify_users(
		recipients,
		"shop_sync_failed",
		"Retailer",
		doc.name,
		subject,
		email_content=email_content,
		dedupe_on=["document_type", "document_name", "subject"],
	)
