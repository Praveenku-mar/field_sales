# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Serves the Field Sales desktop app (Vue SPA) at /fieldsales/* and its alias /fs/* (hooks.website_route_rules)."""

from urllib.parse import quote

import frappe
from frappe import _

no_cache = 1

APP_ROLES = ("System Manager", "Sales Manager", "Field Sales Rep")


def get_context(context):
	if frappe.session.user == "Guest":
		_redirect(f"/login?redirect-to={quote(frappe.request.path if frappe.request else '/fieldsales')}")

	if not has_app_access():
		frappe.throw(_("You do not have access to Field Sales"), frappe.PermissionError)

	# get_csrf_token may create the token on this GET request; keep it.
	frappe.db.commit()
	context.boot = get_boot()
	return context


def has_app_access(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(APP_ROLES) & set(frappe.get_roles(user)))


def get_boot() -> dict:
	"""Values the SPA reads from `window` before it mounts (see frontend/src/main.js)."""
	return frappe._dict(
		{
			"frappe_version": frappe.__version__,
			"site_name": frappe.local.site,
			"csrf_token": frappe.sessions.get_csrf_token(),
			"default_route": _app_base(),
			"sysdefaults": frappe.defaults.get_defaults(),
			# Realtime socket (composables/useRealtime.js): its own port under bench start.
			"socketio_port": frappe.conf.socketio_port,
			"dev_server": frappe._dev_server,
		}
	)


@frappe.whitelist(methods=["GET"], allow_guest=True)
def get_context_for_dev():
	"""
	Boot values for `yarn dev`, where Vite serves index.html instead of this page.

	GET, because the CSRF token is one of the values: a signed-in user's POST
	is refused without the token it is asking for.
	"""
	if not frappe.conf.developer_mode:
		frappe.throw(_("This method is only meant for developer mode"))
	return get_boot()


def _app_base() -> str:
	"""The path the app was opened under: /fs or /fieldsales."""
	path = frappe.request.path if frappe.request else ""
	return "/fs" if path == "/fs" or path.startswith("/fs/") else "/fieldsales"


def _redirect(location: str):
	frappe.local.flags.redirect_location = location
	# 302: a permanent redirect would be cached and keep sending users to login.
	redirect = frappe.Redirect()
	redirect.http_status_code = 302
	raise redirect
