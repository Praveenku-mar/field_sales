# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Daily email to each manager summarising their reps' day, at 20:00 on each store's clock."""

import functools

import frappe
from frappe import _
from frappe.utils import escape_html, fmt_money, formatdate, get_url

from field_sales.field_sales.report.rep_performance.rep_performance import get_rep_stats, get_reps_in_scope
from field_sales.permissions import clear_allowed_reps_cache
from field_sales.utils.money import store_currency
from field_sales.utils.store import (
	current_store,
	for_each_store,
	store_criterion,
	store_mail_args,
	store_now,
)

# Local hour the summary goes out, and how long a store-day's "sent" mark is kept.
SEND_HOUR = 20
SENT_MARK_SECONDS = 2 * 24 * 60 * 60
# How long a run holds a manager's summary while sending it: shorter than the hourly tick, so a
# run that died before its commit (worker killed, timeout) leaves the next run free to send.
CLAIM_SECONDS = 50 * 60


def send_daily_summary():
	"""
	Hourly: for every Store where it is ``SEND_HOUR`` or later on the store's clock, email every
	manager (a user whose Sales Rep has others reporting to it) a summary of the store's today
	for everyone under them, at all levels. Once per manager and local date, so an hour run twice
	(or a slow worker) sends nothing more to a manager who already got it.

	Managers whose team had no activity and no failures get no email. Disabled Stores are
	included (their reps may still have worked today, e.g. while a token refresh is failing),
	but not uninstalled ones: that merchant has left and gets no more email.
	"""
	for_each_store(
		_send_store_summaries,
		title="send_daily_summary",
		enabled_only=False,
		filters={"uninstalled_on": ("is", "not set")},
	)


def _send_store_summaries():
	now = store_now()
	# From SEND_HOUR on (not only during it): a late run (busy workers, a restart) still sends.
	if now.hour < SEND_HOUR:
		return
	today = str(now.date())
	for manager in _get_managers():
		sent, claim = _summary_keys(current_store(), today, manager.user)
		# Sent already (committed), or another run is sending it now (NX claim).
		if frappe.cache.get(sent) or not frappe.cache.set(claim, 1, nx=True, ex=CLAIM_SECONDS):
			continue
		try:
			_send_to_manager(manager, today)
		except Exception:
			frappe.cache.delete(claim)
			# One bad manager record must not stop the others' summaries.
			frappe.log_error(title=f"Field Sales daily summary failed for {manager.user}")
			continue
		# The email is in the Email Queue only once the job commits: marked sent then, so a run
		# that never committed sends it again once its claim expires.
		frappe.db.after_commit.add(functools.partial(frappe.cache.set, sent, 1, ex=SENT_MARK_SECONDS))


def _summary_keys(store: str, today: str, user: str) -> tuple[str, str]:
	"""Redis keys of one manager's summary for one store day: (sent mark, sending claim)."""
	base = frappe.cache.make_key(f"field_sales:daily_summary:{store}:{today}:{user}")
	return base, f"{base}:claim"


def _get_managers() -> list:
	rep = frappe.qb.DocType("Sales Rep")
	sub = frappe.qb.DocType("Sales Rep").as_("sub")
	user = frappe.qb.DocType("User")
	return (
		frappe.qb.from_(rep)
		.join(sub)
		.on(sub.reports_to == rep.name)
		.join(user)
		.on(user.name == rep.user)
		.select(rep.name, rep.user, rep.full_name)
		.distinct()
		.where(rep.active == 1)
		.where(user.enabled == 1)
		.where(store_criterion(rep))
	).run(as_dict=True)


def _send_to_manager(manager, today: str):
	clear_allowed_reps_cache()
	reps = get_reps_in_scope({}, user=manager.user)
	if not reps:
		return
	rows = get_rep_stats(today, today, reps)
	failures = _get_open_failures(list(reps))

	for row in rows:
		row["open_failures"] = failures.get(row["sales_rep"], 0)

	active = [r for r in rows if r["planned_visits"] or r["orders"] or r["open_failures"]]
	if not active:
		return

	frappe.sendmail(
		**store_mail_args(),
		recipients=[manager.user],
		subject=_("Agamsale summary for {0}").format(formatdate(today)),
		message=_render(manager, today, active),
		reference_doctype="Sales Rep",
		reference_name=manager.name,
	)


def _get_open_failures(reps: list[str]) -> dict:
	"""Failed Field Orders + Retailers per rep, whenever they failed (they are still open today)."""
	counts = {}
	for doctype, extra in (("Field Order", {"docstatus": 1}), ("Retailer", {})):
		for row in frappe.get_all(
			doctype,
			filters={"sync_status": "Failed", "sales_rep": ("in", reps), **extra},
			fields=["sales_rep", {"COUNT": "*", "as": "count"}],
			group_by="sales_rep",
		):
			counts[row.sales_rep] = counts.get(row.sales_rep, 0) + row.count
	return counts


def _render(manager, today: str, rows: list[dict]) -> str:
	currency = store_currency()
	totals = {
		key: sum(r[key] for r in rows)
		for key in ("planned_visits", "completed_visits", "orders", "order_value", "open_failures")
	}

	def tr(cells, bold=False):
		tag = "th" if bold else "td"
		return "<tr>" + "".join(
			f'<{tag} style="padding:6px 10px;border:1px solid #e2e6e9;text-align:{align}">{value}</{tag}>'
			for value, align in cells
		) + "</tr>"

	header = tr(
		[
			(_("Sales Rep"), "left"),
			(_("Visits"), "right"),
			(_("Completed"), "right"),
			(_("Orders"), "right"),
			(_("Order Value"), "right"),
			(_("Sync Failures"), "right"),
		],
		bold=True,
	)
	body = "".join(
		tr(
			[
				(escape_html(r["sales_rep_name"] or r["sales_rep"]), "left"),
				(r["planned_visits"], "right"),
				(r["completed_visits"], "right"),
				(r["orders"], "right"),
				(fmt_money(r["order_value"], currency=currency), "right"),
				(
					f'<span style="color:#e24c4c;font-weight:600">{r["open_failures"]}</span>'
					if r["open_failures"]
					else 0,
					"right",
				),
			]
		)
		for r in rows
	)
	total = tr(
		[
			(_("Total"), "left"),
			(totals["planned_visits"], "right"),
			(totals["completed_visits"], "right"),
			(totals["orders"], "right"),
			(fmt_money(totals["order_value"], currency=currency), "right"),
			(totals["open_failures"], "right"),
		],
		bold=True,
	)

	links = f'<a href="{get_url("/app/query-report/Rep%20Performance")}">{_("Rep Performance")}</a>'
	if totals["open_failures"]:
		links += f' · <a href="{get_url("/app/sync-failures")}">{_("Sync Failures")}</a>'

	return f"""
		<p>{_("Hi {0},").format(escape_html(manager.full_name or manager.user))}</p>
		<p>{_("Here is your team's day ({0}).").format(formatdate(today))}</p>
		<table style="border-collapse:collapse;font-size:13px">{header}{body}{total}</table>
		<p style="color:#8d99a6;font-size:12px">{_("Sync Failures are records still failing, whenever they failed.")}</p>
		<p>{links}</p>
	"""
