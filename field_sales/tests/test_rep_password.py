# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.core.doctype.user.user import User, update_password
from frappe.tests import IntegrationTestCase
from frappe.utils.password import check_password, update_password as set_user_password

from field_sales.api import send_sales_rep_password_reset, set_sales_rep_password

NEW_PASSWORD = "Fs-Reset#2026-strong"
OLD_PASSWORD = "Fs-Old#2026-strong"


class TestRepPassword(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.manager_user = self.make_user("pwreset.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.admin_user = self.make_user("pwreset.admin@example.com", ["System Manager"])
		self.rep_user = self.make_user("pwreset.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep.name)
		# A user with other roles, linked as a rep under the manager (create_sales_rep allows this).
		self.accounts_user = self.make_user("pwreset.accounts@example.com", ["Field Sales Rep", "Accounts User"])
		self.accounts_rep = self.make_rep(self.accounts_user, reports_to=self.manager_rep.name)
		# A manager below the manager, and a plain rep outside the manager's tree.
		self.sub_manager_user = self.make_user("pwreset.submanager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.sub_manager_rep = self.make_rep(self.sub_manager_user, reports_to=self.manager_rep.name)
		self.outsider_user = self.make_user("pwreset.outsider@example.com", ["Field Sales Rep"])
		self.outsider_rep = self.make_rep(self.outsider_user)
		self.ensure_outgoing_email()

	def tearDown(self):
		frappe.set_user("Administrator")
		# IntegrationTestCase only rolls back once per class.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def make_user(self, email, roles):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user.name

	def make_rep(self, user, reports_to=None):
		return frappe.get_doc({"doctype": "Sales Rep", "user": user, "reports_to": reports_to}).insert(
			ignore_permissions=True
		)

	def ensure_outgoing_email(self):
		if frappe.get_all("Email Account", filters={"enable_outgoing": 1, "default_outgoing": 1}, limit=1):
			return
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": "PW Reset Test",
				"email_id": "pwreset.outgoing@example.com",
				"smtp_server": "smtp.example.com",
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"no_smtp_authentication": 1,
			}
		).insert(ignore_permissions=True)

	def as_user(self, acting_user, fn, *args, **kwargs):
		frappe.set_user(acting_user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def set_directly(self, acting_user, rep, **kwargs):
		return self.as_user(acting_user, set_sales_rep_password, rep.name, NEW_PASSWORD, logout_all_sessions=0, **kwargs)

	def send_link(self, acting_user, rep, **kwargs):
		return self.as_user(acting_user, send_sales_rep_password_reset, rep.name, **kwargs)

	def reset_key(self, user):
		return frappe.db.get_value("User", user, "reset_password_key")

	def assert_password_set(self, user):
		self.assertEqual(check_password(user, NEW_PASSWORD, delete_tracker_cache=False), user)

	# -- direct set --------------------------------------------------------

	def test_manager_cannot_set_someone_elses_password(self):
		with self.assertRaises(frappe.PermissionError):
			self.set_directly(self.manager_user, self.rep)

	def test_system_manager_sets_password_directly(self):
		self.set_directly(self.admin_user, self.rep)
		self.set_directly(self.admin_user, self.accounts_rep)
		self.assert_password_set(self.rep_user)
		self.assert_password_set(self.accounts_user)

	def test_rep_changes_own_password_with_current_one(self):
		set_user_password(self.rep_user, OLD_PASSWORD)
		self.set_directly(self.rep_user, self.rep, old_password=OLD_PASSWORD)
		self.assert_password_set(self.rep_user)

	# -- reset link --------------------------------------------------------

	def test_manager_sends_link_to_plain_rep_and_link_works_once(self):
		links = []
		with patch.object(User, "password_reset_mail", lambda self, link: links.append(link)):
			result = self.send_link(self.manager_user, self.rep)

		self.assertEqual(result, {"email": self.rep_user})
		self.assertEqual(len(links), 1)
		key = links[0].split("key=", 1)[1]

		frappe.set_user("Guest")
		frappe.local.response = frappe._dict()
		with patch.object(frappe.local, "login_manager", MagicMock(), create=True):
			update_password(NEW_PASSWORD, key=key)
			self.assert_password_set(self.rep_user)
			# A used link no longer finds the user.
			message = update_password("Fs-Again#2026-strong", key=key)
		self.assertIn("used before or is invalid", message)
		self.assertEqual(frappe.local.response.http_status_code, 410)

	def test_reset_link_email_is_queued(self):
		self.send_link(self.manager_user, self.rep)
		self.assertTrue(frappe.db.exists("Email Queue Recipient", {"recipient": self.rep_user}))

	def test_link_can_sign_rep_out(self):
		with (
			patch.object(User, "password_reset_mail"),
			patch("field_sales.api.clear_sessions") as clear,
		):
			self.send_link(self.manager_user, self.rep, logout_all_sessions=1)
		clear.assert_called_once_with(user=self.rep_user, force=True)

	def test_link_refused_outside_tree(self):
		with self.assertRaises(frappe.PermissionError):
			self.send_link(self.manager_user, self.outsider_rep)

	def test_link_refused_to_manager(self):
		with self.assertRaises(frappe.PermissionError):
			self.send_link(self.manager_user, self.sub_manager_rep)

	def test_link_refused_to_user_with_other_roles(self):
		with self.assertRaises(frappe.PermissionError):
			self.send_link(self.manager_user, self.accounts_rep)

	def test_link_refused_for_own_account(self):
		with self.assertRaises(frappe.ValidationError):
			self.send_link(self.manager_user, self.manager_rep)

	def test_link_refused_for_disabled_user(self):
		frappe.db.set_value("User", self.rep_user, "enabled", 0)
		with self.assertRaises(frappe.ValidationError):
			self.send_link(self.manager_user, self.rep)
		self.assertFalse(self.reset_key(self.rep_user))

	def test_no_outgoing_email_sends_nothing(self):
		frappe.db.set_value("Email Account", {"name": ("is", "set")}, {"enable_outgoing": 0, "default_outgoing": 0})
		with patch("frappe.email.doctype.email_account.email_account.EmailAccount.find_from_config", return_value=None):
			with self.assertRaises(frappe.ValidationError) as raised:
				self.send_link(self.manager_user, self.rep)
		self.assertIn("Outgoing email is not set up", str(raised.exception))
		self.assertFalse(self.reset_key(self.rep_user))
		self.assertFalse(frappe.db.exists("Email Queue Recipient", {"recipient": self.rep_user}))

	def test_enabled_but_not_default_account_is_not_enough(self):
		# Frappe only sends through the default outgoing account (or site config's mail_server).
		frappe.db.set_value("Email Account", {"name": ("is", "set")}, {"enable_outgoing": 1, "default_outgoing": 0})
		with patch("frappe.email.doctype.email_account.email_account.EmailAccount.find_from_config", return_value=None):
			with self.assertRaises(frappe.ValidationError) as raised:
				self.send_link(self.manager_user, self.rep)
		self.assertIn("Outgoing email is not set up", str(raised.exception))

	def test_system_manager_sends_link_to_any_rep(self):
		with patch.object(User, "password_reset_mail"):
			self.assertEqual(self.send_link(self.admin_user, self.accounts_rep), {"email": self.accounts_user})
		self.assertTrue(self.reset_key(self.accounts_user))
