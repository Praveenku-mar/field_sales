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
	"""Enabled users with a monitoring role; cached for the rest of the request / job."""
	users = getattr(frappe.local, "field_sales_monitor_users", None)
	if users is None:
		has_role = frappe.qb.DocType("Has Role")
		user = frappe.qb.DocType("User")
		users = (
			frappe.qb.from_(has_role)
			.join(user)
			.on(user.name == has_role.parent)
			.select(has_role.parent)
			.distinct()
			.where(has_role.parenttype == "User")
			.where(has_role.role.isin(MONITOR_ROLES))
			.where(user.enabled == 1)
			.where(user.name != "Guest")
		).run(pluck=True)
		frappe.local.field_sales_monitor_users = users
	return users
