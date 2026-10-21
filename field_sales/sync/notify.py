# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Realtime "sync finished" events for open forms and the Sync Failures page."""

import frappe

# Roles that can open the Sync Failures page.
MONITOR_ROLES = ("Sales Manager", "System Manager")


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
	for user in _monitor_users():
		frappe.publish_realtime(event, message, user=user, after_commit=True)


def _monitor_users() -> list[str]:
	"""Enabled users who can open the Sync Failures page."""
	return users_with_roles(MONITOR_ROLES)


def users_with_roles(roles: tuple[str, ...]) -> list[str]:
	"""Enabled users with any of ``roles``; cached for the rest of the request / job."""
	cache = getattr(frappe.local, "field_sales_role_users", None)
	if cache is None:
		cache = frappe.local.field_sales_role_users = {}
	key = tuple(sorted(roles))
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
			.where(has_role.role.isin(key))
			.where(user.enabled == 1)
			.where(user.name != "Guest")
		).run(pluck=True)
	return cache[key]
