# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Create the vendor staff role (``utils/store.py -> VENDOR_ROLE``).

On the shared site vendor staff are users of no store holding this role; a System Manager
without it reaches no store. The role is not given to anyone here: granting it to every System
Manager of no store would bring back the "no store = every store" rule this role replaces.
Vendor staff get it by hand (docs/multi-merchant/rollout.md), and
``bench --site <site> field-sales-check-shared`` lists System Managers left without either.

Safe to run again. Also run by ``install.after_install``.
"""

import frappe

from field_sales.utils.store import VENDOR_ROLE


def execute():
	if frappe.db.exists("Role", VENDOR_ROLE):
		return
	role = frappe.new_doc("Role")
	role.role_name = VENDOR_ROLE
	# Vendor staff work in Desk (support, rollout checks) as well as in the app.
	role.desk_access = 1
	role.flags.ignore_mandatory = role.flags.ignore_permissions = True
	role.insert()
