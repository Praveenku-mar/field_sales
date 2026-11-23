# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The merchant's admin account, set up right after a Shopify install (shared site).

An install signs the Shopify admin in as an automatic ``shopify-admin-<code>@<site>`` user, which
has no email or password of its own. The app then asks for the merchant's admin email
(``needs_admin_setup``). ``set_store_admin`` makes that email the store's admin: a user in the
store with Store Admin, Sales Manager and Field Sales Rep, and the store's Shopify sign-in
(``Store.embedded_user``). It gets an invitation with a password-setup link; once the password
is set they are sent to the login page (``update_password``), so they sign in to the web app and
the mobile app with the same account.

Single-store sites keep their embedded user as before.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.core.doctype.user.user import update_password as _frappe_update_password

from field_sales.permissions import STORE_ADMIN_ROLE
from field_sales.utils.store import (
	assign_user_to_store,
	can_be_embedded_user,
	get_store_name,
	get_store_value,
	is_multi_store,
	is_vendor,
	set_store_value,
	store_scope,
	user_store,
)

# Where a store's user goes once they have set their password from an invitation or reset link.
LOGIN_AFTER_PASSWORD = "/login?redirect-to=%2Ffieldsales"


def needs_admin_setup(store: str | None = None) -> bool:
	"""On the shared site: the store's Shopify sign-in is still the automatic user (or none)."""
	if not is_multi_store():
		return False
	from field_sales.shopify.embedded import embedded_email

	store = get_store_name(store)
	if not store:
		return False
	current = get_store_value("embedded_user", store=store)
	return not current or current == embedded_email(store)


def store_admin_email(store: str | None = None) -> str | None:
	"""The merchant's admin account, once set (``None`` while it is still the automatic user)."""
	store = get_store_name(store)
	if not store or needs_admin_setup(store):
		return None
	return get_store_value("embedded_user", store=store)


@frappe.whitelist(methods=["POST"])
def set_store_admin(email: str) -> dict:
	"""
	Make ``email`` the current store's admin: the user (created if new) gets Store Admin, a Sales
	Rep with Sales Manager and Field Sales Rep, this store, and becomes its Shopify sign-in. An
	invitation with a password-setup link is sent. Also used to change the admin later.
	"""
	store = _require_store_admin()
	email = (email or "").strip().lower()
	frappe.utils.validate_email_address(email, throw=True)
	# Never Administrator, Guest, vendor staff or another store's user.
	if not can_be_embedded_user(email, store):
		frappe.throw(_("{0} cannot be this store's admin. Use another email.").format(email))

	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		_ensure_admin_user(email, store)
		set_store_value("embedded_user", email, store)
	finally:
		frappe.set_user(previous_user)
	send_admin_invite(email, store)
	return {"email": email, "invited": True}


@frappe.whitelist(methods=["POST"])
def resend_admin_invite() -> dict:
	"""Send the store's admin a new password-setup link."""
	store = _require_store_admin()
	email = store_admin_email(store)
	if not email:
		frappe.throw(_("This store has no admin email yet"))
	send_admin_invite(email, store)
	return {"email": email, "invited": True}


def send_admin_invite(user: str, store: str) -> None:
	"""
	The invitation: Frappe's one-time ``/update-password?key=…`` link.

	Sent by Field Sales itself, from the site's default outgoing account under its own name: no
	store's name as sender and no store's reply-to (unlike the store's own mail, which uses
	``store_mail_args``). The store named in it is ``store``, never just the current one.
	"""
	from field_sales.api import _retailer_login_url

	link = frappe.get_doc("User", user)._reset_password(send_email=False)
	store_name = _store_name(store)
	frappe.sendmail(
		recipients=[user],
		subject=_("Set up your Agamsale admin account for {0}").format(store_name),
		template="store_admin_invite",
		args={"store_name": store_name, "user": user, "link": link, "login_url": _retailer_login_url()},
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def update_password(
	new_password: str, logout_all_sessions: int = 0, key: str | None = None, old_password: str | None = None
):
	"""
	Frappe's ``update_password`` (``override_whitelisted_methods``), unchanged, except that a
	store's user who set their password from an invitation or reset link (``key``) is signed out
	and sent to the login page, to sign in with the new password. Everyone else, and a password
	change while signed in, gets Frappe's own result.
	"""
	result = _frappe_update_password(
		new_password, logout_all_sessions=logout_all_sessions, key=key, old_password=old_password
	)
	user = frappe.session.user
	if key and user not in (None, "", "Guest") and user_store(user):
		frappe.local.login_manager.logout()
		return LOGIN_AFTER_PASSWORD
	return result


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _store_name(store: str) -> str:
	"""``store``'s name for the invitation: its company, its shop's name, else its shop domain or code."""
	values = frappe.db.get_value(
		"Store", store, ["company_name", "shop_name", "store_url"], as_dict=True
	) or frappe._dict()
	return values.company_name or values.shop_name or values.store_url or store


def _require_store_admin() -> str:
	"""The current store, for its Store Admin (or vendor staff); refuses everyone else."""
	if not is_multi_store():
		frappe.throw(_("Only on the shared site"), frappe.PermissionError)
	store = get_store_name(required=True)
	if is_vendor():
		return store
	if STORE_ADMIN_ROLE in frappe.get_roles() and store_scope() == store:
		return store
	frappe.throw(_("Only the store's admin can set its admin email"), frappe.PermissionError)


def _ensure_admin_user(email: str, store: str) -> None:
	"""The user with Store Admin in ``store`` and a Sales Rep that is a manager and a rep."""
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		user.enabled = 1  # e.g. disabled by shop/redact, now the store is back
		if STORE_ADMIN_ROLE not in {r.role for r in user.roles}:
			user.append("roles", {"role": STORE_ADMIN_ROLE})
		user.save(ignore_permissions=True)
	else:
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": STORE_ADMIN_ROLE}],
			}
		).insert(ignore_permissions=True)
	assign_user_to_store(email, store)

	rep_name = frappe.db.get_value("Sales Rep", {"user": email}, "name")
	if rep_name:
		rep = frappe.get_doc("Sales Rep", rep_name)
		rep.update({"sales_manager": 1, "sales_rep": 1, "active": 1})
	else:
		rep = frappe.get_doc(
			{
				"doctype": "Sales Rep",
				"user": email,
				"full_name": frappe.db.get_value("User", email, "full_name") or email,
				"store": store,
				"active": 1,
				"sales_manager": 1,
				"sales_rep": 1,
			}
		)
	# The store's admin is not one of the reps its plan counts (billing.usage leaves out the
	# store's embedded user, which this account becomes).
	rep.flags.ignore_plan_limit = True
	rep.save(ignore_permissions=True)
