# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Review finding F6: the Shopify plan's limits held only on the app's own paths. A Sales Rep inserted
through Desk/REST, or an inactive one ticked Active, was never counted; and orders were counted by
their editable ``order_date``, so one dated last month escaped this month's limit.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_days, add_months, get_first_day, now_datetime, nowdate

from field_sales.shopify import billing
from field_sales.tests.test_data_protection import DataProtectionCase
from field_sales.utils.limits import ORDERS_PER_MONTH, SALES_REPS

PLAN = {"plan": "starter", "trial_ends_at": None, "known": True}


class TestPlanLimitBypass(DataProtectionCase):
	def setUp(self):
		super().setUp()
		self.users = 0
		frappe.db.set_single_value(
			"Store Connection",
			{"shopify_auth_mode": "OAuth", "store_url": "plan-shop.myshopify.com", "embedded_user": None},
		)
		patcher = patch.object(billing, "get_active_plan", return_value=PLAN)
		patcher.start()
		self.addCleanup(patcher.stop)

	def limit(self, **limits):
		frappe.db.set_single_value("Store Connection", "plan_limits", frappe.as_json({"starter": limits}))

	def user(self):
		self.users += 1
		email = f"plan.rep{self.users}@example.com"
		user = frappe.get_doc({"doctype": "User", "email": email, "first_name": f"Plan{self.users}", "send_welcome_email": 0})
		user.insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		return email

	def rep(self, active=1, **flags):
		doc = frappe.get_doc({"doctype": "Sales Rep", "user": self.user(), "active": active})
		doc.flags.update(flags)
		return doc.insert(ignore_permissions=True)

	# -- Sales reps ------------------------------------------------------------------

	def test_a_rep_inserted_directly_is_counted(self):
		self.limit(sales_reps=billing.usage(SALES_REPS))
		with self.assertRaises(frappe.ValidationError):
			self.rep()

	def test_reactivating_a_rep_is_counted(self):
		inactive = self.rep(active=0)  # an inactive rep is not counted, so it may be added
		self.limit(sales_reps=billing.usage(SALES_REPS))

		inactive.active = 1
		with self.assertRaises(frappe.ValidationError):
			inactive.save(ignore_permissions=True)

	def test_editing_an_active_rep_is_not_blocked_at_the_limit(self):
		rep = self.rep()
		self.limit(sales_reps=billing.usage(SALES_REPS))
		rep.territory = None
		rep.monthly_target = 5000
		rep.save(ignore_permissions=True)

	def test_the_embedded_admins_rep_is_not_limited(self):
		self.limit(sales_reps=billing.usage(SALES_REPS))
		self.rep(ignore_plan_limit=True)

	# -- Orders per month ----------------------------------------------------------------

	def submitted_order(self, order_date, submitted_on):
		retailer = self.retailer("Plan Stores")
		rep = self.rep(ignore_plan_limit=True)
		name = (
			frappe.get_doc(
				{"doctype": "Field Order", "retailer": retailer, "sales_rep": rep.name, "order_date": order_date}
			)
			.insert(ignore_permissions=True, ignore_mandatory=True)
			.name
		)
		frappe.db.set_value(
			"Field Order", name, {"docstatus": 1, "submitted_on": submitted_on}, update_modified=False
		)
		return name

	def test_orders_count_by_submission_not_order_date(self):
		before = billing.usage(ORDERS_PER_MONTH)
		last_month = add_months(get_first_day(nowdate()), -1)

		# Dated last month but submitted now: counts this month.
		self.submitted_order(order_date=last_month, submitted_on=now_datetime())
		self.assertEqual(billing.usage(ORDERS_PER_MONTH), before + 1)

		# Dated today but submitted last month: last month's.
		self.submitted_order(order_date=nowdate(), submitted_on=add_days(last_month, 3))
		self.assertEqual(billing.usage(ORDERS_PER_MONTH), before + 1)

	def test_submit_records_the_server_time(self):
		frappe.db.set_single_value("Store Connection", "plan_limits", None)
		retailer = self.retailer("Submit Stores")
		rep = self.rep(ignore_plan_limit=True)
		product = frappe.get_doc(
			{
				"doctype": "Platform Product",
				"platform": "Shopify",
				"product_title": "Plan product",
				"external_product_id": "plan-1",
				"external_variant_id": "plan-1-v",
				"status": "Active",
				"price": 10,
				"track_inventory": 0,
			}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": retailer,
				"sales_rep": rep.name,
				"order_date": add_months(nowdate(), -1),
				"submitted_on": "2020-01-01 00:00:00",  # a client's value is never kept
				"items": [{"product": product.name, "qty": 1, "rate": 10}],
			}
		).insert(ignore_permissions=True)
		started = now_datetime()
		with patch("field_sales.field_sales.doctype.field_order.field_order.enqueue_push_order", create=True):
			doc.submit()

		self.assertGreaterEqual(frappe.utils.get_datetime(doc.submitted_on), started.replace(microsecond=0))
