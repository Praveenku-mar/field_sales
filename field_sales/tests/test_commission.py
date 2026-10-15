# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_months, flt, get_first_day, get_last_day, nowdate

from field_sales.commission.engine import create_entries, reconcile_order
from field_sales.commission.api import (
	approve_statement,
	get_team_commission,
	assign_commission_plan,
	create_adjustment,
	generate_statements,
	get_commission_plans,
	save_commission_plan,
	get_commission_statement,
	get_commission_statements,
	get_my_commission_summary,
)
from field_sales.commission.statements import build_statement, mark_paid
from field_sales.connectors import get_connector
from field_sales.connectors.shopify import _normalize_order
from field_sales.field_sales.report.rep_commission_summary.rep_commission_summary import execute as run_summary
from field_sales.sync.webhooks import process_event

SHOP = "test-shop.myshopify.com"
GET_ORDER = "field_sales.connectors.shopify.ShopifyConnector.get_order"
CURRENCY = "INR"


class TestCommission(IntegrationTestCase):
	def setUp(self):
		# set_single_value does not validate; everything rolls back after each test.
		frappe.db.set_single_value(
			"Store Connection",
			{"platform": "Shopify", "enabled": 1, "store_url": SHOP, "shopify_api_version": "2026-10"},
		)
		self.conn = get_connector()
		self.products = {}
		self.users = 0
		self.retailer = frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Commission Test Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Coimbatore",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		# IntegrationTestCase only rolls back once per class: undo this test's rows here.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- builders ----------------------------------------------------------

	def product(self, variant_id: str, **values):
		if variant_id not in self.products:
			self.products[variant_id] = frappe.get_doc(
				{
					"doctype": "Platform Product",
					"platform": "Shopify",
					"product_title": f"Commission product {variant_id}",
					"external_product_id": f"90{variant_id}",
					"external_variant_id": variant_id,
					"status": "Active",
					"price": 100,
					**values,
				}
			).insert(ignore_permissions=True)
		return self.products[variant_id]

	def plan(self, name: str, rate_rows=(), slabs=(), **values):
		doc = frappe.get_doc(
			{
				"doctype": "Commission Plan",
				"plan_name": name,
				"is_active": 1,
				"rule_type": "Flat %",
				"commission_on": "Paid",
				**values,
			}
		)
		for row in rate_rows:
			doc.append("rates", row)
		for row in slabs:
			doc.append("slabs", row)
		return doc.insert(ignore_permissions=True)

	def rep(self, plan=None, max_discount_pct=0, reports_to=None, active=1):
		self.users += 1
		email = f"commission.rep{self.users}@example.com"
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": f"CommRep{self.users}", "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles("Field Sales Rep")
		return frappe.get_doc(
			{
				"doctype": "Sales Rep",
				"user": email,
				"max_discount_pct": max_discount_pct,
				"reports_to": reports_to,
				"commission_plan": plan.name if plan else None,
				"commission_active": active,
			}
		).insert(ignore_permissions=True)

	def field_order(self, rep, lines: list[tuple[str, float, float]], order_id: str):
		"""A submitted Field Order with the platform order id set (the push is not run)."""
		doc = frappe.get_doc(
			{
				"doctype": "Field Order",
				"retailer": self.retailer.name,
				"sales_rep": rep.name,
				"order_date": nowdate(),
				"items": [
					{
						"product": self.product(variant).name,
						"qty": qty,
						"rate": rate,
						"external_variant_id": variant,
					}
					for variant, qty, rate in lines
				],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Field Order", doc.name, {"docstatus": 1, "external_order_id": order_id, "platform": "Shopify"})
		return doc.name

	def store_order(self, order_id: str, lines: list[dict], financial="paid", fulfillment="unfulfilled", refunds=(), cancelled=False):
		"""A neutral order as ``get_order`` returns it. Each line: variant, qty, price and optional discount / tags / type."""
		return {
			"external_order_id": order_id,
			"currency": CURRENCY,
			"financial_status": financial,
			"fulfillment_status": fulfillment,
			"cancelled": cancelled,
			"lines": [
				{
					"line_id": f"L{i}",
					"external_variant_id": line["variant"],
					"qty": line["qty"],
					"current_qty": line["qty"],
					"unit_price": line["price"],
					"discount": line.get("discount", 0),
					"product_type": line.get("type"),
					"tags": line.get("tags", []),
				}
				for i, line in enumerate(lines, start=1)
			],
			"refunds": list(refunds),
		}

	def refund(self, refund_id: str, *quantities: tuple[str, float]):
		return {"refund_id": refund_id, "lines": [{"line_id": line, "qty": qty, "amount": 0} for line, qty in quantities]}

	def reconcile(self, order: dict):
		with patch(GET_ORDER, return_value=order):
			return reconcile_order(order["external_order_id"])

	def entries(self, **filters):
		return frappe.get_all(
			"Commission Entry",
			filters=filters,
			fields=["*"],
			order_by="creation, name",
		)

	# -- 1 flat rate with penalty -------------------------------------------

	def test_flat_rate_with_discount_penalty(self):
		plan = self.plan("Flat penalty", flat_rate=5, penalty_factor=0.5)
		rep = self.rep(plan, max_discount_pct=10)
		self.field_order(rep, [("1", 2, 1000)], "7001")

		# 2 x 1000 = 2000 less 100 discount: a 5% discount on a base of 1900.
		self.reconcile(self.store_order("7001", [{"variant": "1", "qty": 2, "price": 1000, "discount": 100}]))

		(entry,) = self.entries(entry_type="Order")
		self.assertEqual(entry.beneficiary_type, "Rep")
		self.assertEqual(flt(entry.base_amount), 1900)
		self.assertEqual(flt(entry.discount_pct), 5)
		self.assertEqual(flt(entry.base_rate), 5)
		self.assertEqual(flt(entry.effective_rate), 3.75)
		self.assertEqual(flt(entry.amount), 71.25)
		self.assertEqual(entry.status, "Earned")
		self.assertEqual(str(entry.earned_on), nowdate())

	# -- 2 product rate precedence -------------------------------------------

	def test_product_rate_precedence(self):
		# Rows deliberately listed lowest precedence first: the order of rows must not decide.
		both = self.product("1", collection="Snacks")
		plan = self.plan(
			"Product rates",
			rule_type="Product Rate",
			default_rate=1,
			rate_rows=[
				{"match_type": "Tag", "match_value": "premium", "rate": 4},
				{"match_type": "Collection/Product Type", "match_value": "snacks", "rate": 3},
				{"match_type": "Product", "product": both.name, "rate": 2},
			],
		)
		rep = self.rep(plan)
		self.product("2", collection="Snacks")
		self.product("3")
		self.product("4")
		self.field_order(rep, [("1", 1, 100), ("2", 1, 100), ("3", 1, 100), ("4", 1, 100)], "7002")

		lines = [
			{"variant": "1", "qty": 1, "price": 100, "tags": ["Premium"]},  # product + collection + tag -> product
			{"variant": "2", "qty": 1, "price": 100, "tags": ["Premium"]},  # collection + tag -> collection
			{"variant": "3", "qty": 1, "price": 100, "tags": ["PREMIUM"]},  # tag only -> tag
			{"variant": "4", "qty": 1, "price": 100},  # nothing -> default
		]
		self.reconcile(self.store_order("7002", lines))

		rates = {self.products_by_name(e.product): flt(e.base_rate) for e in self.entries(entry_type="Order")}
		self.assertEqual(rates, {"1": 2, "2": 3, "3": 4, "4": 1})

	def products_by_name(self, name: str) -> str:
		return next(v for v, p in self.products.items() if p.name == name)

	def test_product_type_from_the_store_counts_as_collection(self):
		plan = self.plan(
			"Type rates",
			rule_type="Product Rate",
			default_rate=1,
			rate_rows=[{"match_type": "Collection/Product Type", "match_value": "Beverages", "rate": 6}],
		)
		rep = self.rep(plan)
		self.field_order(rep, [("1", 1, 100)], "7003")
		self.reconcile(self.store_order("7003", [{"variant": "1", "qty": 1, "price": 100, "type": "beverages"}]))
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual(flt(entry.amount), 6)

	# -- 3 slabs at statement time -------------------------------------------

	def slab_setup(self, slab_mode: str):
		plan = self.plan(
			f"Slabs {slab_mode}",
			rule_type="Tiered Slab",
			slab_mode=slab_mode,
			slabs=[
				{"from_amount": 0, "to_amount": 1000, "rate": 2},
				{"from_amount": 1000, "to_amount": 5000, "rate": 3},
				{"from_amount": 5000, "to_amount": 0, "rate": 5},
			],
		)
		rep = self.rep(plan)
		self.field_order(rep, [("1", 6, 1000)], "7004")
		self.reconcile(self.store_order("7004", [{"variant": "1", "qty": 6, "price": 1000}]))
		today = nowdate()
		return rep, build_statement(rep.name, get_first_day(today), get_last_day(today))

	def test_slab_whole_amount(self):
		rep, statement = self.slab_setup("Whole Amount")
		# 6000 of sales falls in the 5% band, and 5% applies to all of it.
		self.assertEqual(flt(statement.total_sales), 6000)
		self.assertEqual(flt(statement.slab_rate_applied), 5)
		self.assertEqual(flt(statement.gross_commission), 300)
		self.assertEqual(flt(statement.net_payable), 300)
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual(flt(entry.amount), 300)
		self.assertEqual(entry.commission_statement, statement.name)

	def test_slab_marginal(self):
		rep, statement = self.slab_setup("Marginal")
		# 1000 x 2% + 4000 x 3% + 1000 x 5% = 20 + 120 + 50.
		self.assertEqual(flt(statement.gross_commission), 190)
		self.assertEqual(flt(statement.net_payable), 190)
		self.assertEqual(flt(statement.slab_rate_applied), flt(190 / 6000 * 100, 4))
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual(flt(entry.amount), 190)

	# -- 4 manager override --------------------------------------------------

	def test_manager_override_two_levels_up(self):
		top = self.rep(self.plan("Top", flat_rate=1, override_pct=0.5))
		senior = self.rep(self.plan("Senior", flat_rate=1, override_pct=1), reports_to=top.name)
		manager = self.rep(self.plan("Manager", flat_rate=1, override_pct=2), reports_to=senior.name)
		rep = self.rep(self.plan("Rep", flat_rate=5, override_levels=2), reports_to=manager.name)
		self.field_order(rep, [("1", 1, 1000)], "7005")

		self.reconcile(self.store_order("7005", [{"variant": "1", "qty": 1, "price": 1000}]))

		by_rep = {e.sales_rep: e for e in self.entries(entry_type="Order")}
		# Two levels above the rep: the manager and the senior, each at their own plan's %; not the top.
		self.assertEqual(set(by_rep), {rep.name, manager.name, senior.name})
		self.assertEqual(flt(by_rep[rep.name].amount), 50)
		self.assertEqual(by_rep[manager.name].beneficiary_type, "Manager Override")
		self.assertEqual(flt(by_rep[manager.name].amount), 20)
		self.assertEqual(flt(by_rep[senior.name].amount), 10)
		self.assertEqual(by_rep[manager.name].source_rep, rep.name)
		self.assertEqual(by_rep[senior.name].source_rep, rep.name)

	# -- 5 paid -> Earned, partial refund ------------------------------------

	def test_paid_earns_and_partial_refund_reverses_proportionally(self):
		rep = self.rep(self.plan("Refund plan", flat_rate=10))
		self.field_order(rep, [("1", 4, 250)], "7006")
		line = [{"variant": "1", "qty": 4, "price": 250}]

		self.reconcile(self.store_order("7006", line, financial="pending"))
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual((entry.status, flt(entry.amount), entry.earned_on), ("Pending", 100, None))

		self.reconcile(self.store_order("7006", line, financial="paid"))
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual(entry.status, "Earned")

		# 1 of 4 units refunded: a quarter of the commission is reversed.
		refunded = self.store_order("7006", line, financial="partially_refunded", refunds=[self.refund("R1", ("L1", 1))])
		self.reconcile(refunded)
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual((entry.status, flt(entry.reversed_amount)), ("Earned", 25))

		# Everything refunded: fully reversed.
		everything = self.store_order(
			"7006", line, financial="refunded", refunds=[self.refund("R1", ("L1", 1)), self.refund("R2", ("L1", 3))]
		)
		self.reconcile(everything)
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual((entry.status, flt(entry.reversed_amount)), ("Reversed", 100))

	# -- 6 refund after the statement is paid --------------------------------

	def test_refund_after_paid_statement_books_adjustment(self):
		rep = self.rep(self.plan("Adjust plan", flat_rate=10))
		self.field_order(rep, [("1", 4, 250)], "7007")
		line = [{"variant": "1", "qty": 4, "price": 250}]
		self.reconcile(self.store_order("7007", line))

		today = nowdate()
		statement = build_statement(rep.name, get_first_day(today), get_last_day(today))
		statement.submit()
		mark_paid(statement.name, payment_reference="UTR-1")
		self.assertEqual(frappe.db.get_value("Commission Statement", statement.name, "status"), "Paid")

		refunded = self.store_order("7007", line, financial="partially_refunded", refunds=[self.refund("R1", ("L1", 1))])
		self.reconcile(refunded)

		original = frappe.db.get_value(
			"Commission Entry",
			{"entry_type": "Order"},
			["status", "amount", "reversed_amount", "commission_statement"],
			as_dict=True,
		)
		self.assertEqual(original.status, "Earned")
		self.assertEqual((flt(original.amount), flt(original.reversed_amount)), (100, 0))
		self.assertEqual(original.commission_statement, statement.name)

		(adjustment,) = self.entries(entry_type="Adjustment")
		self.assertEqual(flt(adjustment.amount), -25)
		self.assertEqual(adjustment.sales_rep, rep.name)
		self.assertTrue(adjustment.adjusts_entry)
		self.assertTrue(adjustment.adjustment_reason)
		self.assertFalse(adjustment.commission_statement)

		# The same refund again adds nothing; a bigger one adds only the difference.
		self.reconcile(refunded)
		self.assertEqual(len(self.entries(entry_type="Adjustment")), 1)
		more = self.store_order("7007", line, financial="partially_refunded", refunds=[self.refund("R1", ("L1", 2))])
		self.reconcile(more)
		self.assertEqual(sorted(flt(a.amount) for a in self.entries(entry_type="Adjustment")), [-25, -25])

		# The paid statement is untouched.
		self.assertEqual(flt(frappe.db.get_value("Commission Statement", statement.name, "net_payable")), 100)

	# -- 7 replayed webhook ---------------------------------------------------

	def webhook_event(self, topic: str, payload: dict) -> dict:
		headers = {"X-Shopify-Topic": topic, "X-Shopify-Webhook-Id": frappe.generate_hash(length=10)}
		return self.conn.parse_webhook(headers, json.dumps(payload).encode())

	def test_replayed_paid_webhook_does_not_double_count(self):
		rep = self.rep(self.plan("Replay plan", flat_rate=10))
		self.field_order(rep, [("1", 4, 250)], "7008")
		order = self.store_order("7008", [{"variant": "1", "qty": 4, "price": 250}])

		event = self.webhook_event("orders/paid", {"id": 7008})
		self.assertEqual(event["topic"], "order/paid")

		with patch(GET_ORDER, return_value=order):
			first = process_event("Shopify", event)
			second = process_event("Shopify", self.webhook_event("orders/paid", {"id": 7008}))

		self.assertEqual(first["created"], 1)
		self.assertEqual(second["created"], 0)
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual((entry.status, flt(entry.amount)), ("Earned", 100))

		# A refunds/create event names the order as order_id.
		refunded = self.store_order("7008", [{"variant": "1", "qty": 4, "price": 250}], refunds=[self.refund("R1", ("L1", 1))])
		with patch(GET_ORDER, return_value=refunded):
			process_event("Shopify", self.webhook_event("refunds/create", {"id": 1, "order_id": 7008}))
			process_event("Shopify", self.webhook_event("refunds/create", {"id": 1, "order_id": 7008}))
		(entry,) = self.entries(entry_type="Order")
		self.assertEqual(flt(entry.reversed_amount), 25)

	# -- 8 non Field Sales order ----------------------------------------------

	def test_order_not_from_field_sales_is_ignored(self):
		rep = self.rep(self.plan("Ignore plan", flat_rate=10))
		self.field_order(rep, [("1", 1, 100)], "7009")

		with patch(GET_ORDER) as get_order:
			result = process_event("Shopify", self.webhook_event("orders/paid", {"id": 424242}))

		self.assertIn("Ignored", result["note"])
		get_order.assert_not_called()
		self.assertEqual(self.entries(), [])

	# -- 9 same order reconciled from several deliveries -----------------------

	def test_same_order_reconciled_repeatedly_creates_entries_once(self):
		manager = self.rep(self.plan("Mgr", flat_rate=1, override_pct=2))
		rep = self.rep(self.plan("Dedupe plan", flat_rate=10, override_levels=1), reports_to=manager.name)
		order_name = self.field_order(rep, [("1", 1, 100), ("2", 2, 50)], "7010")
		other_name = self.field_order(rep, [("1", 1, 100)], "7011")
		first = self.store_order("7010", [{"variant": "1", "qty": 1, "price": 100}, {"variant": "2", "qty": 2, "price": 50}])
		second = self.store_order("7011", [{"variant": "1", "qty": 1, "price": 100}])

		def fake(external_id):
			return {"7010": first, "7011": second}[external_id]

		with patch(GET_ORDER, side_effect=fake):
			# paid, fulfilled and a push-time call all land on the same order.
			reconcile_order("7010")
			process_event("Shopify", self.webhook_event("orders/fulfilled", {"id": 7010}))
			process_event("Shopify", self.webhook_event("orders/paid", {"id": 7010}))
			create_entries(order_name)
			reconcile_order("7011")
			create_entries(other_name)

		# Two lines x (rep + manager override) for the first order; one pair for the second.
		self.assertEqual(len(self.entries(source_order=order_name)), 4)
		self.assertEqual(len(self.entries(source_order=other_name)), 2)
		self.assertEqual(len({e.dedupe_key for e in self.entries()}), 6)
		self.assertEqual(sum(flt(e.amount) for e in self.entries(source_order=order_name, beneficiary_type="Rep")), 20)

	# -- order with a rep who is not on commission -----------------------------

	def test_rep_without_commission_gets_no_entries(self):
		rep = self.rep(self.plan("Off plan", flat_rate=10), active=0)
		self.field_order(rep, [("1", 1, 100)], "7012")
		result = self.reconcile(self.store_order("7012", [{"variant": "1", "qty": 1, "price": 100}]))
		self.assertEqual(result["created"], 0)
		self.assertEqual(self.entries(), [])

	# -- Rep Commission Summary report -------------------------------------------

	def test_rep_commission_summary(self):
		manager = self.rep(self.plan("Rpt mgr", flat_rate=1, override_pct=2))
		rep = self.rep(self.plan("Rpt plan", flat_rate=10, override_levels=1), reports_to=manager.name)
		self.field_order(rep, [("1", 4, 250)], "7013")
		line = [{"variant": "1", "qty": 4, "price": 250, "discount": 100}]
		self.reconcile(self.store_order("7013", line, refunds=[self.refund("R1", ("L1", 1))]))
		today = nowdate()
		filters = {"from_date": get_first_day(today), "to_date": get_last_day(today)}

		_columns, rows = run_summary({**filters, "sales_rep": rep.name})
		(row,) = rows
		# Base 900 at 10% = 90; a quarter refunded = 22.50.
		self.assertEqual((row["sales"], row["earned"], row["reversed"], row["pending"]), (900, 90, 22.5, 0))
		self.assertEqual(flt(row["avg_discount"], 2), flt(100 / 1000 * 100, 2))
		self.assertEqual(row["net"], 67.5)

		_columns, rows = run_summary({**filters, "manager": manager.name})
		self.assertEqual([r["sales_rep"] for r in rows], [rep.name])
		_columns, rows = run_summary({**filters, "sales_rep": manager.name})
		self.assertEqual((rows[0]["override"], rows[0]["net"]), (13.5, 13.5))
		self.assertEqual(run_summary({**filters, "location_id": "no-such-location"})[1], [])

	# -- Statement screens API ---------------------------------------------------

	def draft_statement(self, rep):
		"""A Draft statement with one entry (an empty one has no currency on a bare test site)."""
		order_id = f"79{self.users}"
		self.field_order(rep, [("1", 4, 250)], order_id)
		self.reconcile(self.store_order(order_id, [{"variant": "1", "qty": 4, "price": 250}]))
		today = nowdate()
		return build_statement(rep.name, get_first_day(today), get_last_day(today))

	def test_my_summary_without_rep_is_empty(self):
		# Administrator has no Sales Rep of their own.
		self.assertEqual(get_my_commission_summary(), {"has_rep": False})

	def test_approve_statement_sets_approved(self):
		rep = self.rep(self.plan("Approve plan", flat_rate=10))
		statement = self.draft_statement(rep)

		result = approve_statement(statement.name)
		self.assertEqual(result["status"], "Approved")
		self.assertEqual(frappe.db.get_value("Commission Statement", statement.name, "docstatus"), 1)
		with self.assertRaises(frappe.ValidationError):
			approve_statement(statement.name)

		data = get_commission_statement(statement.name)
		self.assertEqual(data["full_name"], rep.full_name)
		self.assertTrue(data["can_manage"])
		self.assertEqual(len(data["entries"]), 1)

	def test_rep_cannot_approve_statement(self):
		rep = self.rep(self.plan("Deny plan", flat_rate=10))
		statement = self.draft_statement(rep)
		frappe.set_user(rep.user)
		try:
			with self.assertRaises(frappe.PermissionError):
				approve_statement(statement.name)
			self.assertFalse(get_commission_statement(statement.name)["can_manage"])
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Commission Statement", statement.name, "docstatus"), 0)

	def test_statement_list_respects_hierarchy(self):
		manager = self.rep(self.plan("List mgr", flat_rate=1))
		member = self.rep(self.plan("List member", flat_rate=1), reports_to=manager.name)
		outsider = self.rep(self.plan("List outsider", flat_rate=1))
		own, team, other = (self.draft_statement(r).name for r in (manager, member, outsider))

		def visible(user):
			frappe.set_user(user)
			try:
				return {r["name"] for r in get_commission_statements()["statements"]}
			finally:
				frappe.set_user("Administrator")

		self.assertEqual(visible(manager.user) & {own, team, other}, {own, team})
		self.assertEqual(visible(member.user) & {own, team, other}, {team})
		self.assertTrue({own, team, other} <= visible("Administrator"))
		frappe.set_user(manager.user)
		try:
			(row,) = [r for r in get_commission_statements()["statements"] if r["name"] == team]
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(row["full_name"], member.full_name)

	# -- Manager screens API -------------------------------------------------------

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def test_save_commission_plan_creates_and_edits(self):
		created = save_commission_plan(
			{
				"plan_name": "Screen plan",
				"rule_type": "Tiered Slab",
				"commission_on": "Paid",
				"slab_mode": "Marginal",
				"slabs": [
					{"from_amount": 0, "to_amount": 1000, "rate": 2},
					{"from_amount": 1000, "to_amount": 0, "rate": 3},
				],
				"rates": [{"match_type": "Tag", "match_value": "ignored", "rate": 1}],
				"is_active": 1,
			}
		)
		self.assertEqual(created["name"], "Screen plan")
		self.assertEqual(len(created["slabs"]), 2)
		self.assertEqual(created["rates"], [])

		edited = save_commission_plan(
			{"name": "Screen plan", "plan_name": "Renamed?", "rule_type": "Flat %", "flat_rate": 5, "is_active": 0}
		)
		self.assertEqual((edited["name"], edited["plan_name"], edited["rule_type"], edited["slabs"]), ("Screen plan", "Screen plan", "Flat %", []))
		self.assertEqual(frappe.db.get_value("Commission Plan", "Screen plan", "is_active"), 0)
		self.assertIn("Screen plan", [p["name"] for p in get_commission_plans()])

		with self.assertRaises(frappe.ValidationError):
			save_commission_plan({"plan_name": "Bad plan", "rule_type": "Flat %", "flat_rate": 0})

	def test_rep_cannot_manage_plans_or_adjustments(self):
		plan = self.plan("Guard plan", flat_rate=5)
		rep = self.rep(plan)
		for fn, args in (
			(save_commission_plan, ({"plan_name": "Nope", "rule_type": "Flat %", "flat_rate": 5},)),
			(get_commission_plans, ()),
			(assign_commission_plan, (rep.name, plan.name, 0)),
			(create_adjustment, (rep.name, 10, "Bonus")),
			(generate_statements, (nowdate()[:7],)),
		):
			with self.assertRaises(frappe.PermissionError, msg=fn.__name__):
				self.as_user(rep.user, fn, *args)
		self.assertFalse(frappe.db.exists("Commission Plan", "Nope"))

	def test_assign_commission_plan(self):
		rep = self.rep(None, active=0)
		plan = self.plan("Assign plan", flat_rate=5)
		result = assign_commission_plan(rep.name, plan.name, 1)
		self.assertEqual((result["commission_plan"], result["commission_active"]), (plan.name, 1))
		self.assertEqual(frappe.db.get_value("Sales Rep", rep.name, "commission_active"), 1)

		with self.assertRaises(frappe.ValidationError):
			assign_commission_plan(rep.name, None, 1)
		frappe.db.set_value("Commission Plan", plan.name, "is_active", 0)
		with self.assertRaises(frappe.ValidationError):
			assign_commission_plan(rep.name, plan.name, 1)

	def test_team_lists_reps_without_commission_with_their_plan(self):
		plan = self.plan("Team plan", flat_rate=5)
		rep = self.rep(plan, active=0)
		(row,) = [r for r in get_team_commission("all") if r["sales_rep"] == rep.name]
		self.assertEqual((row["commission_plan"], row["commission_active"], row["net"]), ("Team plan", 0, 0))

	def test_create_adjustment(self):
		frappe.db.set_default("currency", CURRENCY)
		rep = self.rep(self.plan("Adj plan", flat_rate=5))
		entry = create_adjustment(rep.name, 150, "Quarterly bonus")
		create_adjustment(rep.name, -40, "Correction", beneficiary_type="Manager Override")

		doc = frappe.get_doc("Commission Entry", entry["name"])
		self.assertEqual((doc.entry_type, doc.status, doc.beneficiary_type, doc.earned_on), ("Adjustment", "Earned", "Rep", doc.posting_date))
		self.assertEqual((doc.currency, doc.commission_plan, doc.adjustment_reason), (CURRENCY, "Adj plan", "Quarterly bonus"))

		frappe.set_user(rep.user)
		try:
			summary = get_my_commission_summary()
		finally:
			frappe.set_user("Administrator")
		self.assertEqual((summary["earned"], summary["reversed"], summary["net"]), (150, 40, 110))

		with self.assertRaises(frappe.ValidationError):
			create_adjustment(rep.name, 10, "  ")
		with self.assertRaises(frappe.ValidationError):
			create_adjustment(rep.name, 0, "Nothing")

	def test_generate_statements(self):
		frappe.db.set_default("currency", CURRENCY)
		plan = self.plan("Gen plan", flat_rate=5)
		with_entries = self.rep(plan)
		without_entries = self.rep(plan)
		inactive = self.rep(plan, active=0)
		last_month = add_months(nowdate(), -1)
		month = str(last_month)[:7]
		for rep in (with_entries, inactive):
			create_adjustment(rep.name, 25, "Bonus", posting_date=str(get_first_day(last_month)))

		result = generate_statements(month)
		self.assertEqual(len(result["created"]), 1)
		statement = frappe.get_doc("Commission Statement", result["created"][0])
		self.assertEqual((statement.sales_rep, statement.status, statement.net_payable), (with_entries.name, "Draft", 25))
		self.assertEqual([s["rep"] for s in result["skipped"]], [without_entries.name])

		again = generate_statements(month)
		self.assertEqual(again["created"], [])
		self.assertEqual({s["rep"] for s in again["skipped"]}, {with_entries.name, without_entries.name})

		# A named rep is processed even when not commission-active.
		single = generate_statements(month, inactive.name)
		self.assertEqual(len(single["created"]), 1)

		with self.assertRaises(frappe.ValidationError):
			generate_statements("2026-13")

	# -- Shopify order normalization -------------------------------------------

	def test_normalize_shopify_order(self):
		gid = "gid://shopify/"
		node = {
			"id": f"{gid}Order/5001",
			"displayFinancialStatus": "PARTIALLY_REFUNDED",
			"displayFulfillmentStatus": "PARTIALLY_FULFILLED",
			"cancelledAt": None,
			"currencyCode": "INR",
			"lineItems": {
				"nodes": [
					{
						"id": f"{gid}LineItem/11",
						"quantity": 3,
						"currentQuantity": 2,
						"originalUnitPriceSet": {"shopMoney": {"amount": "250.00"}},
						"discountAllocations": [
							{"allocatedAmountSet": {"shopMoney": {"amount": "10.00"}}},
							{"allocatedAmountSet": {"shopMoney": {"amount": "5.50"}}},
						],
						"variant": {"id": f"{gid}ProductVariant/21"},
						"product": {"id": f"{gid}Product/31", "productType": "Snacks", "tags": ["premium", "new"]},
					},
					# A custom line (the draft's tax line): no variant, no product.
					{
						"id": f"{gid}LineItem/12",
						"quantity": 1,
						"currentQuantity": 1,
						"originalUnitPriceSet": {"shopMoney": {"amount": "5.00"}},
						"discountAllocations": [],
						"variant": None,
						"product": None,
					},
				]
			},
			"refunds": [
				{
					"id": f"{gid}Refund/41",
					"refundLineItems": {
						"nodes": [
							{
								"lineItem": {"id": f"{gid}LineItem/11"},
								"quantity": 1,
								"subtotalSet": {"shopMoney": {"amount": "250.00"}},
							}
						]
					},
				}
			],
		}

		order = _normalize_order(node)

		self.assertEqual(order["external_order_id"], "5001")
		self.assertEqual(order["currency"], "INR")
		self.assertEqual(order["financial_status"], "partially_refunded")
		self.assertEqual(order["fulfillment_status"], "partial")
		self.assertFalse(order["cancelled"])
		self.assertEqual(
			order["lines"][0],
			{
				"line_id": "11",
				"external_variant_id": "21",
				"qty": 3,
				"current_qty": 2,
				"unit_price": 250,
				"discount": 15.5,
				"product_type": "Snacks",
				"tags": ["premium", "new"],
			},
		)
		self.assertIsNone(order["lines"][1]["external_variant_id"])
		self.assertEqual(order["lines"][1]["tags"], [])
		self.assertEqual(order["refunds"], [{"refund_id": "41", "lines": [{"line_id": "11", "qty": 1, "amount": 250}]}])

		self.assertEqual(_normalize_order(None), {})
		self.assertTrue(_normalize_order({**node, "cancelledAt": "2026-10-15T10:00:00Z"})["cancelled"])
