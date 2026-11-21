# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Test data for two (or more) stores on one site: ``make_store_team("ISA")`` builds a Store with its
users and one record of each store doctype, so isolation tests can check that a user of one store
reaches nothing of another.
"""

import frappe
from frappe.utils import nowdate

from field_sales.utils.store import clear_store_cache, clear_user_store_cache, use_store


def make_user(email: str, roles: list[str]) -> str:
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		user.add_roles(*roles)
		return user.name
	user = frappe.get_doc(
		{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
	)
	user.insert(ignore_permissions=True)
	user.add_roles(*roles)
	return user.name


def make_store(code: str):
	return frappe.get_doc(
		{
			"doctype": "Store",
			"store_code": code,
			"platform": "Shopify",
			"store_url": f"{code.lower()}-isolation.myshopify.com",
		}
	).insert(ignore_permissions=True)


def make_store_team(code: str) -> frappe._dict:
	"""A store and, inside it: admin, manager, rep and retailer login users plus records."""
	frappe.set_user("Administrator")
	make_store(code)
	clear_store_cache()
	low = code.lower()
	numeric = "".join(str(ord(c)) for c in code)
	team = frappe._dict(store=code)
	with use_store(code):
		team.admin = make_user(f"{low}.admin@example.com", ["Store Admin"])
		team.manager = make_user(f"{low}.manager@example.com", ["Sales Manager", "Field Sales Rep"])
		team.rep_user = make_user(f"{low}.rep@example.com", ["Field Sales Rep"])
		from field_sales.utils.store import assign_user_to_store

		assign_user_to_store(team.admin, code)

		manager_rep = frappe.get_doc(
			{"doctype": "Sales Rep", "user": team.manager, "store": code, "sales_manager": 1, "sales_rep": 1}
		).insert(ignore_permissions=True)
		team.manager_rep = manager_rep.name
		rep = frappe.get_doc(
			{"doctype": "Sales Rep", "user": team.rep_user, "store": code, "reports_to": manager_rep.name}
		).insert(ignore_permissions=True)
		team.rep = rep.name

		retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"store": code,
				"retailer_name": f"{code} Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Coimbatore",
				"sales_rep": rep.name,
			}
		).insert(ignore_permissions=True)
		team.retailer = retailer.name
		team.retailer_user = make_user(f"{low}.customer@example.com", ["Field Sales Retailer"])
		frappe.db.set_value("Retailer", retailer.name, "user", team.retailer_user)
		assign = frappe.get_doc("Retailer", retailer.name)
		assign.flags.ignore_sync = True
		assign.save(ignore_permissions=True)

		team.visit = (
			frappe.get_doc(
				{
					"doctype": "Visit",
					"store": code,
					"sales_rep": rep.name,
					"retailer": retailer.name,
					"visit_date": nowdate(),
					"status": "Planned",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

		product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"store": code,
				"platform": "Shopify",
				"product_title": f"{code} Product",
				"external_product_id": f"9{numeric}01",
				"external_variant_id": f"9{numeric}01",
				"status": "Active",
				"price": 100,
			}
		).insert(ignore_permissions=True)
		team.product = product.name
		team.stock = (
			frappe.get_doc(
				{
					"doctype": "Product Stock",
					"store": code,
					"product": product.name,
					"location_id": f"L-{code}",
					"location_name": "Main",
					"available_qty": 10,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

		order = frappe.get_doc(
			{
				"doctype": "Field Order",
				"store": code,
				"retailer": retailer.name,
				"sales_rep": rep.name,
				"order_date": nowdate(),
				"items": [{"product": product.name, "qty": 1, "rate": 100}],
			}
		)
		order.flags.ignore_links = True
		order.insert(ignore_permissions=True)
		team.order = order.name

		team.plan = (
			frappe.get_doc(
				{
					"doctype": "Commission Plan",
					"store": code,
					"plan_name": "Standard",
					"rule_type": "Flat %",
					"flat_rate": 5,
					"is_active": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

		team.sync_log = (
			frappe.get_doc(
				{"doctype": "Sync Log", "store": code, "operation": "Product Sync", "status": "Success"}
			)
			.insert(ignore_permissions=True)
			.name
		)

		for doctype, extra in (
			("Commission Entry", {"sales_rep": rep.name}),
			("Commission Statement", {"sales_rep": rep.name}),
			("Payment Transaction", {"reference_order": order.name}),
			("Privacy Request", {}),
		):
			doc = frappe.get_doc({"doctype": doctype, "store": code, **extra})
			doc.flags.ignore_validate = True
			doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)
			team[frappe.scrub(doctype)] = doc.name

	reset_request_caches()
	return team


def reset_request_caches() -> None:
	"""Per-request caches that a test switching users in one request must clear."""
	from field_sales.permissions import clear_allowed_reps_cache
	from field_sales.sync.notify import clear_role_users_cache

	clear_user_store_cache()
	clear_allowed_reps_cache()
	clear_role_users_cache()
