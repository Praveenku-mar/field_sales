# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt
"""
Fixed accounts and records for the mobile app's device integration suite
(field_sales_mobile/integration_test). Idempotent; re-running resets the
fixture visits and orders to their starting state.

Run only against a disposable site that opts in through site_config.json:

	bench --site it.localhost set-config field_sales_integration_site 1
	bench --site it.localhost execute field_sales.tests.integration_fixtures.seed \
		--kwargs '{"password": "<IT_PASSWORD>"}'

The guard matters: this creates users with a shared, known password.
"""

import frappe
from frappe.utils import nowdate
from frappe.utils.password import update_password

DOMAIN = "fieldsales.test"
USERS = {
	"admin": ("it.admin", ["System Manager"]),
	"manager": ("it.manager", ["Field Sales Rep", "Sales Manager"]),
	"rep_a": ("it.rep.a", ["Field Sales Rep"]),
	"rep_b": ("it.rep.b", ["Field Sales Rep"]),
	"retailer": ("it.retailer", ["Field Sales Retailer"]),
}
# Store A is where the device GPS is placed (see scripts/run_integration_tests.sh).
STORE_A = {"retailer_name": "IT Store A", "city": "Chennai", "latitude": 13.0827, "longitude": 80.2707}
STORE_B = {"retailer_name": "IT Store B", "city": "Madurai", "latitude": 9.9252, "longitude": 78.1198}
PRODUCT_BARCODE = "8901234567890"
MARKER = "[integration-fixture]"


def seed(password: str, commit: bool = True) -> dict:
	if not frappe.conf.get("field_sales_integration_site"):
		frappe.throw("Refusing to seed: set field_sales_integration_site in this site's config first.")
	if not password or len(password) < 12:
		frappe.throw("Pass a password of at least 12 characters.")

	frappe.set_user("Administrator")
	_ensure_role("Field Sales Retailer")
	users = {key: _user(f"{local}@{DOMAIN}", roles, password) for key, (local, roles) in USERS.items()}

	manager = _rep(users["manager"], "IT Manager", reports_to=None, sales_manager=1)
	rep_a = _rep(users["rep_a"], "IT Rep A", reports_to=manager)
	# Rep B is outside the manager's team: neither the manager nor rep A may see its rows.
	rep_b = _rep(users["rep_b"], "IT Rep B", reports_to=None)

	store_a = _retailer(STORE_A, rep_a, user=users["retailer"])
	store_b = _retailer(STORE_B, rep_b)
	product = _product()

	visits = {"rep_a": _visit(rep_a, store_a), "rep_b": _visit(rep_b, store_b)}
	orders = {
		"rep_a_draft": _order(rep_a, store_a, product, submitted=False),
		"rep_a_submitted": _order(rep_a, store_a, product, submitted=True),
		"rep_b_draft": _order(rep_b, store_b, product, submitted=False),
	}
	if commit:
		frappe.db.commit()
	return {
		"users": users,
		"reps": {"manager": manager, "rep_a": rep_a, "rep_b": rep_b},
		"retailers": {"store_a": store_a, "store_b": store_b},
		"product": product,
		"visits": visits,
		"orders": orders,
	}


def _ensure_role(name):
	if not frappe.db.exists("Role", name):
		frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 0}).insert(ignore_permissions=True)


def _user(email, roles, password):
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	user.add_roles(*roles)
	update_password(email, password)
	return user.name


def _rep(user, full_name, reports_to, sales_manager=0):
	name = frappe.db.get_value("Sales Rep", {"user": user})
	values = {"full_name": full_name, "reports_to": reports_to, "sales_manager": sales_manager, "sales_rep": 1, "active": 1}
	if name:
		frappe.db.set_value("Sales Rep", name, values)
		return name
	return frappe.get_doc({"doctype": "Sales Rep", "user": user, **values}).insert(ignore_permissions=True).name


def _retailer(spec, sales_rep, user=None):
	values = {
		**spec,
		"sales_rep": sales_rep,
		"phone": "9876543210",
		"address_line1": "1 Integration Street",
		"geofence_radius_m": 200,
		"status": "Active",
	}
	name = frappe.db.get_value("Retailer", {"retailer_name": spec["retailer_name"]})
	if name:
		frappe.db.set_value("Retailer", name, values)
	else:
		name = frappe.get_doc({"doctype": "Retailer", **values}).insert(ignore_permissions=True).name
	if user:
		frappe.db.set_value("Retailer", name, "user", user)
	return name


def _product():
	name = frappe.db.get_value("Platform Product", {"barcode": PRODUCT_BARCODE})
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "IT Scan Product",
				"external_product_id": "it-900",
				"external_variant_id": "it-90",
				"barcode": PRODUCT_BARCODE,
				"sku": "IT-SCAN-1",
				"status": "Active",
				"price": 100,
				"track_inventory": 0,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _visit(sales_rep, retailer):
	"""A planned visit for today, reset so check-in can run again."""
	for old in frappe.get_all("Visit", {"sales_rep": sales_rep, "notes": MARKER}, pluck="name"):
		frappe.delete_doc("Visit", old, ignore_permissions=True, force=True)
	return (
		frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": sales_rep,
				"retailer": retailer,
				"visit_date": nowdate(),
				"status": "Planned",
				"visit_purpose": "Order Taking",
				"notes": MARKER,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _order(sales_rep, retailer, product, submitted):
	remark = f"{MARKER} {'submitted' if submitted else 'draft'}"
	existing = frappe.db.get_value("Field Order", {"sales_rep": sales_rep, "remarks": remark})
	if existing:
		if submitted:
			# Payment tests mark it paid; put it back.
			frappe.db.set_value(
				"Field Order",
				existing,
				{"payment_status": "Unpaid", "amount_paid": 0, "razorpay_payment_link_id": None,
				 "razorpay_payment_link_url": None},
			)
		return existing
	order = frappe.get_doc(
		{
			"doctype": "Field Order",
			"retailer": retailer,
			"sales_rep": sales_rep,
			"order_date": nowdate(),
			"remarks": remark,
			"items": [{"product": product, "qty": 1, "rate": 100}],
		}
	).insert(ignore_permissions=True)
	if submitted:
		# Submitted without a store push, as the Razorpay tests do.
		frappe.db.set_value("Field Order", order.name, {"docstatus": 1, "sync_status": "Synced"})
	return order.name
