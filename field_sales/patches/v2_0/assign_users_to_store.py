# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Put the users of a single-store site into its Store (one store per user).

- Every enabled user holding Sales Manager / Field Sales Rep / Field Sales Retailer, or being the
  Store's ``embedded_user``, gets the store's default User Permission (``assign_user_to_store``).
- The embedded user also gets the Store Admin role.
- System Managers are left alone, also when they hold one of those roles: with no store they
  are vendor staff (on a single-store site, the site's owner).

Only on a site with exactly one Store (a shared site's users are assigned as they are created).
Safe to run again. Also run by ``install.after_install``.
"""

import frappe

from field_sales.permissions import STORE_ADMIN_ROLE
from field_sales.utils.store import assign_user_to_store

STORE_USER_ROLES = ("Sales Manager", "Field Sales Rep", "Field Sales Retailer")


def execute():
	frappe.reload_doc("field_sales", "doctype", "store")
	stores = frappe.get_all("Store", pluck="name")
	if len(stores) != 1:
		return
	store = stores[0]

	users = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "role": ("in", STORE_USER_ROLES)},
			pluck="parent",
		)
	)
	embedded = frappe.db.get_value("Store", store, "embedded_user")
	if embedded:
		users.add(embedded)

	system_managers = set(
		frappe.get_all("Has Role", filters={"parenttype": "User", "role": "System Manager"}, pluck="parent")
	)
	for user in sorted(users - system_managers):
		if user in ("Administrator", "Guest") or not frappe.db.get_value("User", user, "enabled"):
			continue
		other = frappe.db.get_value("User Permission", {"user": user, "allow": "Store"}, "for_value")
		if other and other != store:
			continue
		assign_user_to_store(user, store)

	if embedded and frappe.db.exists("User", embedded) and frappe.db.exists("Role", STORE_ADMIN_ROLE):
		user_doc = frappe.get_doc("User", embedded)
		if STORE_ADMIN_ROLE not in {r.role for r in user_doc.roles}:
			user_doc.append("roles", {"role": STORE_ADMIN_ROLE})
			user_doc.save(ignore_permissions=True)
