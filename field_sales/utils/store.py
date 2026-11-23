# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
The Store a request or job works for.

One Store record per merchant holds every per-store setting (connection, tokens, locations,
invoice details, feature switches). Vendor-wide settings stay on the Store Connection single.

Which store:

- ``store`` passed explicitly wins;
- else the current store (``set_current_store()`` / ``use_store()``, set per request or job);
- else, on a site with exactly one Store, that one. This keeps single-store sites working with
  no context at all. With several Stores and no context, ``get_store_name()`` raises.

``is_multi_store()`` is the site_config flag ``field_sales_multi_store``: the shared App Store
site, where Razorpay and Confirmed order mode are off (App Store requirement 1.1.2).
"""

from __future__ import annotations

import datetime
import re
from contextlib import contextmanager
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.utils import get_first_day, get_last_day, get_system_timezone, getdate, now_datetime

from field_sales.utils.db import safe_commit

STORE_DOCTYPE = "Store"
_LOCAL_KEY = "field_sales_store"
# The site's only Store, cached for the request/job (cleared when a Store is added or removed).
_ONLY_KEY = "field_sales_only_store"
# {user: store} for the request (user_store()).
_USER_STORES_KEY = "field_sales_user_stores"
CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,4}$")


class NoStoreContext(frappe.ValidationError):
	"""Several stores exist and nothing said which one to use."""


def is_multi_store() -> bool:
	"""True on the shared multi-merchant site (site_config ``field_sales_multi_store``)."""
	return bool(frappe.conf.get("field_sales_multi_store"))


def set_current_store(store: str | None) -> None:
	"""Set (or clear, with ``None``) the store for the rest of this request or job."""
	setattr(frappe.local, _LOCAL_KEY, store or None)


def current_store() -> str | None:
	"""The store set for this request or job, or ``None``."""
	return getattr(frappe.local, _LOCAL_KEY, None)


@contextmanager
def use_store(store: str | None):
	"""Run a block for ``store``, restoring the previous store afterwards."""
	previous = current_store()
	set_current_store(store)
	try:
		yield store
	finally:
		set_current_store(previous)


def enqueue_in_store(method: str, store: str | None = None, **kwargs):
	"""
	``frappe.enqueue`` a job that runs inside ``store`` (default: the store of this request/job).

	Jobs queued by a webhook (Guest) or by Administrator have no user store to fall back on, so the
	store travels with the job. ``kwargs`` are ``frappe.enqueue`` options (``queue``, ``job_id``,
	``deduplicate``, ``enqueue_after_commit``, …) plus the job's own arguments. ``store`` is this
	helper's own: it is not passed to the job (the job reads ``current_store()``).

	Not a ``before_job`` hook popping a ``store`` argument: Frappe retries a deadlocked job with
	the same (already popped) kwargs, so the store would be gone on the retry.
	"""
	return frappe.enqueue(
		"field_sales.utils.store.run_in_store",
		store_method=method,
		store_name=get_store_name(store),
		**kwargs,
	)


def record_store(doctype: str, name: str) -> str | None:
	"""The ``store`` of one record (for queueing a job about it in its own store)."""
	return frappe.db.get_value(doctype, name, "store")


def run_in_store(store_method: str, store_name: str | None = None, **kwargs):
	"""
	Job entry for ``enqueue_in_store``: run ``store_method`` with ``store_name`` as the current store.

	Called directly (not ``frappe.call``), so an argument the job does not take raises, as with
	a plain ``frappe.enqueue``, instead of being dropped.
	"""
	with use_store(store_name):
		return frappe.get_attr(store_method)(**kwargs)


def for_each_store(
	fn, title: str | None = None, enabled_only: bool = True, filters: dict | None = None
) -> None:
	"""
	Scheduled-job helper: call ``fn()`` once per Store (oldest first) with that store current.

	Each store runs inside its own savepoint: a failure undoes only that store's work, is logged
	(Error Log "Field Sales scheduled job failed: {title} ({store})") and the next store still runs.
	The job's own commit at the end keeps the rest; ``fn`` may also commit as it goes. If any store
	failed, the others' work is committed and the job then fails, so the Scheduled Job Log shows it.

	:param title: The job's name for the Error Log (default: ``fn.__name__``).
	:param enabled_only: Skip disabled Stores (uninstalled, or a failed token refresh).
	:param filters: More ``Store`` filters (e.g. skip uninstalled stores).
	"""
	title = title or fn.__name__
	filters = {**({"enabled": 1} if enabled_only else {}), **(filters or {})}
	failed = []
	for store in frappe.get_all(STORE_DOCTYPE, filters=filters, pluck="name", order_by="creation asc"):
		save_point = f"field_sales_store_{frappe.generate_hash(length=10)}"
		frappe.db.savepoint(save_point)
		try:
			with use_store(store):
				fn()
		except Exception:
			_undo_store_work(save_point)
			frappe.log_error(title=f"Field Sales scheduled job failed: {title} ({store})")
			failed.append(store)
			continue
		_release_savepoint(save_point)
	if failed:
		# Keep the other stores' work and the Error Logs: failing the job rolls back what is not committed.
		safe_commit()
		frappe.throw(_("Scheduled job {0} failed for {1}").format(title, ", ".join(failed)))


def _release_savepoint(save_point: str) -> None:
	"""Release a finished store's savepoint (gone already if ``fn`` committed)."""
	try:
		frappe.db.release_savepoint(save_point)
	except Exception:
		pass  # fn committed: the transaction, and with it the savepoint, has ended


def _undo_store_work(save_point: str) -> None:
	"""Roll back to ``save_point``; if ``fn`` committed since (the savepoint is gone), roll back what is left."""
	try:
		frappe.db.rollback(save_point=save_point)
	except Exception:
		frappe.db.rollback()


def clear_store_cache() -> None:
	"""Forget the cached "only Store" (called when a Store is inserted, renamed or deleted)."""
	setattr(frappe.local, _ONLY_KEY, None)


def user_store(user: str | None = None) -> str | None:
	"""
	The Store a user belongs to: their ``User Permission`` on Store (``assign_user_to_store()``),
	or ``None`` for vendor staff and for users not assigned yet. Cached for the request.

	The permission also makes Frappe filter every list, report, form and REST call of a doctype
	with a ``store`` link to that store.
	"""
	user = user or getattr(frappe.session, "user", None)
	if not user or user in ("Administrator", "Guest"):
		return None
	cache = getattr(frappe.local, _USER_STORES_KEY, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, _USER_STORES_KEY, cache)
	if user not in cache:
		cache[user] = frappe.db.get_value(
			"User Permission",
			{"user": user, "allow": STORE_DOCTYPE},
			"for_value",
			order_by="is_default desc, creation asc",
		)
	return cache[user]


def clear_user_store_cache() -> None:
	setattr(frappe.local, _USER_STORES_KEY, None)


def assign_user_to_store(user: str, store: str) -> None:
	"""
	Put a user in a store (one store per user): a default User Permission on Store that applies
	to every doctype. Safe to repeat; refuses a user that already belongs to another store.
	"""
	existing = frappe.get_all(
		"User Permission", filters={"user": user, "allow": STORE_DOCTYPE}, pluck="for_value"
	)
	if existing:
		if store not in existing or len(existing) > 1:
			frappe.throw(
				_("User {0} already belongs to store {1}").format(user, existing[0]),
				frappe.ValidationError,
			)
		return
	frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": STORE_DOCTYPE,
			"for_value": store,
			"apply_to_all_doctypes": 1,
			"is_default": 1,
		}
	).insert(ignore_permissions=True)
	clear_user_store_cache()
	_use_store_timezone(user, store)


def use_store_timezone_for_users(store: str) -> None:
	"""After a store's ``shop_timezone`` changed: its users' Desk follows (shared site only)."""
	clear_timezone_cache()
	users = frappe.get_all(
		"User Permission", filters={"allow": STORE_DOCTYPE, "for_value": store}, pluck="user"
	)
	for user in users:
		_use_store_timezone(user, store)


def _use_store_timezone(user: str, store: str) -> None:
	"""On the shared site, Desk shows the user times in their store's zone (User ``time_zone``)."""
	if not is_multi_store():
		return
	zone = frappe.db.get_value(STORE_DOCTYPE, store, "shop_timezone")
	if _is_zone(zone) and frappe.db.get_value("User", user, "time_zone") != zone:
		frappe.db.set_value("User", user, "time_zone", zone)


def is_vendor(user: str | None = None) -> bool:
	"""Vendor staff: Administrator, or a System Manager who belongs to no store. They see every store."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	if user_store(user):
		return False
	return "System Manager" in frappe.get_roles(user)


def can_be_embedded_user(user: str | None, store: str) -> bool:
	"""
	Whether Shopify admin requests of ``store`` may sign in as ``user``: never Administrator,
	Guest or vendor staff (they would see every store), nor a user of another store.
	"""
	if not user or user in ("Administrator", "Guest") or is_vendor(user):
		return False
	own = user_store(user)
	return not own or own == store


def can_change_connection(user: str | None = None) -> bool:
	"""
	Whether ``user`` may change a Store's connection, app credentials and embedded user: vendor
	staff, and on a single-store site also its own System Managers (they own the whole site).
	Store Admin never does.
	"""
	user = user or frappe.session.user
	if is_vendor(user):
		return True
	return not is_multi_store() and "System Manager" in frappe.get_roles(user)


def user_is_reachable(user: str) -> bool:
	"""
	A user the session user may see or adopt: anyone for vendor staff, else a user of their own
	store or of no store yet (never one that belongs to another store).
	"""
	scope = store_scope()
	own = user_store(user)
	return scope is None or not own or own == scope


def store_scope(user: str | None = None) -> str | None:
	"""
	Which store's records a user may reach:

	- ``None``: every store (vendor staff);
	- a store code: that store only;
	- ``""``: none. On the shared site a user who belongs to no store sees nothing; on a
	  single-store site such a user belongs to its one Store.
	"""
	user = user or frappe.session.user
	if is_vendor(user):
		return None
	store = user_store(user)
	if store:
		return store
	if is_multi_store():
		return ""
	try:
		return get_store_name() or ""
	except NoStoreContext:
		return ""


def get_store_name(store: str | None = None, required: bool = False) -> str | None:
	"""
	The store to work for: ``store``, else the current store, else the session user's own store
	(``user_store()``), else the site's only Store.

	:param required: Raise when there is no Store at all (else return ``None``).
	:raises NoStoreContext: Several stores exist and none was given or set.
	"""
	name = store or current_store() or user_store()
	if name:
		return name
	only = getattr(frappe.local, _ONLY_KEY, None)
	if only:
		return only
	names = frappe.get_all(STORE_DOCTYPE, pluck="name", limit=2, order_by="creation asc")
	if len(names) > 1:
		frappe.throw(_("Several stores exist; no store was given for this request"), NoStoreContext)
	if names:
		setattr(frappe.local, _ONLY_KEY, names[0])
		return names[0]
	if required:
		frappe.throw(_("No store is connected yet"))
	return None


def get_store(store: str | None = None, for_update: bool = False):
	"""
	The Store document (fresh from the database).

	On a site with no Store yet, an unsaved new Store with its defaults, so read-only callers
	behave as they did with an empty Store Connection (e.g. ``enabled`` is 0).
	"""
	name = get_store_name(store)
	if not name:
		return frappe.new_doc(STORE_DOCTYPE)
	return frappe.get_doc(STORE_DOCTYPE, name, for_update=for_update)


def get_store_value(fieldname: str | list[str], store: str | None = None, as_dict: bool = False) -> Any:
	"""One field (or several, with a list) of the store; ``None`` when there is no Store."""
	name = get_store_name(store)
	if not name:
		if isinstance(fieldname, list):
			return frappe._dict() if as_dict else [None] * len(fieldname)
		return None
	return frappe.db.get_value(STORE_DOCTYPE, name, fieldname, as_dict=as_dict)


# ----------------------------------------------------------------------
# The store's clock (design D1: one fixed site clock; the store's zone decides "which day")
# ----------------------------------------------------------------------

_TIMEZONES_KEY = "field_sales_store_timezones"


def store_timezone(store: str | None = None) -> str:
	"""
	The IANA zone "today" and "this month" follow for ``store`` (default: the current store).

	The store's ``shop_timezone`` on the shared multi-store site; else (single-store sites, no
	store in context, or an unknown zone name) the site's own zone, as before.
	"""
	site = get_system_timezone()
	if not is_multi_store():
		return site
	try:
		name = get_store_name(store)
	except NoStoreContext:
		return site  # vendor staff with no store chosen: the site clock
	if not name:
		return site
	cache = getattr(frappe.local, _TIMEZONES_KEY, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, _TIMEZONES_KEY, cache)
	if name not in cache:
		zone = frappe.db.get_value(STORE_DOCTYPE, name, "shop_timezone")
		cache[name] = zone if _is_zone(zone) else site
	return cache[name]


def clear_timezone_cache() -> None:
	"""Forget the request's cached store zones (after a store's ``shop_timezone`` changes)."""
	setattr(frappe.local, _TIMEZONES_KEY, None)


def _is_zone(name: str | None) -> bool:
	if not name:
		return False
	try:
		ZoneInfo(name)
	except (ZoneInfoNotFoundError, ValueError):
		return False
	return True


def site_to_store(value, store: str | None = None) -> datetime.datetime:
	"""A naive site-clock datetime (as stored) as the naive wall time in the store's zone."""
	site, zone = get_system_timezone(), store_timezone(store)
	value = frappe.utils.get_datetime(value)
	if site == zone:
		return value
	return value.replace(tzinfo=ZoneInfo(site)).astimezone(ZoneInfo(zone)).replace(tzinfo=None)


def store_to_site(value, store: str | None = None) -> datetime.datetime:
	"""
	A naive wall time in the store's zone as the naive site-clock datetime, e.g. to filter a
	Datetime field from the start of a store day or month.
	"""
	site, zone = get_system_timezone(), store_timezone(store)
	value = frappe.utils.get_datetime(value)
	if site == zone:
		return value
	return value.replace(tzinfo=ZoneInfo(zone)).astimezone(ZoneInfo(site)).replace(tzinfo=None)


def store_now(store: str | None = None) -> datetime.datetime:
	"""Now as the store's wall clock shows it (naive)."""
	return site_to_store(now_datetime(), store)


def store_today(store: str | None = None) -> datetime.date:
	"""Today's date for the store: the merchant's local date (order, visit and posting dates)."""
	return store_now(store).date()


def store_month_bounds(day=None, store: str | None = None) -> tuple[datetime.date, datetime.date]:
	"""First and last day of the store month holding ``day`` (default: the store's today)."""
	day = getdate(day) if day else store_today(store)
	return get_first_day(day), get_last_day(day)


# Date fields that mean "the merchant's local date", filled on insert when left empty.
STORE_DATE_FIELDS = {
	"Field Order": "order_date",
	"Visit": "visit_date",
	"Commission Entry": "posting_date",
}


def fill_store_dates(doc, method: str | None = None) -> None:
	"""``before_insert`` (after ``stamp_store``): an empty store date becomes the store's today."""
	field = STORE_DATE_FIELDS.get(doc.doctype)
	if field and not doc.get(field):
		doc.set(field, store_today(doc.get("store")))


def set_store_value(values: dict | str, value: Any = None, store: str | None = None) -> None:
	"""Write fields of the store without running its controller (like ``set_single_value``)."""
	if isinstance(values, str):
		values = {values: value}
	name = get_store_name(store, required=True)
	frappe.db.set_value(STORE_DOCTYPE, name, values)


def get_store_password(fieldname: str, store: str | None = None) -> str | None:
	"""A decrypted Password field of the store, or ``None`` if unset."""
	from frappe.utils.password import get_decrypted_password

	name = get_store_name(store)
	if not name:
		return None
	return get_decrypted_password(STORE_DOCTYPE, name, fieldname, raise_exception=False)


def set_store_password(fieldname: str, value: str, store: str | None = None) -> None:
	"""Store an encrypted Password field of the store."""
	from frappe.utils.password import set_encrypted_password

	set_encrypted_password(STORE_DOCTYPE, get_store_name(store, required=True), value, fieldname)


def remove_store_password(fieldname: str, store: str | None = None) -> None:
	"""Delete an encrypted Password field of the store."""
	from frappe.utils.password import remove_encrypted_password

	name = get_store_name(store)
	if name:
		remove_encrypted_password(STORE_DOCTYPE, name, fieldname)


def store_for_shop(shop_domain: str | None) -> str | None:
	"""The Store whose ``store_url`` is ``shop_domain`` (case-insensitive), or ``None``."""
	shop = (shop_domain or "").strip().lower()
	if not shop:
		return None
	return frappe.db.get_value(STORE_DOCTYPE, {"store_url": shop}, "name")


def shop_store(shop_domain: str | None) -> str | None:
	"""
	The Store a request about ``shop_domain`` belongs to (webhook, session token, install).

	- The Store whose shop it is.
	- Else, on a single-store site, the site's Store: "one site, one shop" is then the caller's
	  check, as it was before there were several Stores.
	- Else ``None``: a shop that is not (yet) one of the shared site's Stores, or several Stores
	  and none is this shop's.
	"""
	store = store_for_shop(shop_domain)
	if store or is_multi_store():
		return store
	try:
		return get_store_name()
	except NoStoreContext:
		return None


def make_store_code(source: str | None) -> str:
	"""
	A free store code from a shop name or domain: up to 4 letters/digits, upper case,
	with a number in place of the last characters when taken (``ACME``, ``ACM2``, ...).
	"""
	text = (source or "").split(".")[0]
	base = re.sub(r"[^A-Z0-9]", "", text.upper())[:4] or "ST"
	if len(base) < 2:
		base = (base + "S")[:2]
	if not frappe.db.exists(STORE_DOCTYPE, base):
		return base
	for n in range(2, 1000):
		suffix = str(n)
		candidate = (base[: 4 - len(suffix)] + suffix) if len(base) + len(suffix) > 4 else base + suffix
		if not frappe.db.exists(STORE_DOCTYPE, candidate):
			return candidate
	frappe.throw(_("Could not find a free store code for {0}").format(source))


def stamp_store(doc, method: str | None = None) -> None:
	"""
	``before_insert`` for store-linked doctypes: fill ``store`` from the context when empty, and
	refuse a record for another store than the user's own (vendor staff may create for any).
	"""
	if not doc.get("store"):
		# A record of a rep belongs to the rep's store, else to the current store.
		rep = doc.get("sales_rep") if doc.meta.has_field("sales_rep") else None
		doc.store = (frappe.db.get_value("Sales Rep", rep, "store") if rep else None) or get_store_name()
	scope = store_scope()
	if scope is not None and doc.store != scope:
		frappe.throw(
			_("You cannot create {0} records for another store").format(_(doc.doctype)),
			frappe.PermissionError,
		)


def store_mail_args(store: str | None = None) -> dict:
	"""
	``frappe.sendmail`` arguments that send a store's mail in its name (design D3), on the shared
	site: the site's one outgoing account as sender with the store's name, and replies to the
	store's company email. ``{}`` on single-store sites and without a store (mail as before).
	"""
	if not is_multi_store():
		return {}
	try:
		name = get_store_name(store)
	except NoStoreContext:
		return {}
	if not name:
		return {}
	from email.utils import formataddr

	from frappe.email.doctype.email_account.email_account import EmailAccount

	store_doc = frappe.db.get_value(
		STORE_DOCTYPE, name, ["shop_name", "company_name", "company_email"], as_dict=True
	) or frappe._dict()
	args = {}
	account = EmailAccount.find_default_outgoing()
	if account and account.get("email_id"):
		label = store_doc.company_name or store_doc.shop_name or name
		args["sender"] = formataddr((label, account.email_id))
	if store_doc.company_email:
		args["reply_to"] = store_doc.company_email
	return args


def report_store(filters, user: str | None = None) -> str | None:
	"""
	The store a report covers: the user's own (``""``: none at all), or for vendor staff the
	report's Store filter (``None``: every store, allowed only on single-store sites).

	On the shared site a report must stay in one store: each store's amounts are in its own
	currency, and totals across them would add different currencies together.
	"""
	scope = store_scope(user)
	if scope is not None:
		return scope
	chosen = (filters or {}).get("store")
	if chosen:
		return chosen
	if is_multi_store():
		frappe.throw(_("Pick a Store: each store's amounts are in its own currency"))
	return None


def store_doctypes() -> list[str]:
	"""The business doctypes that carry a ``store`` link (hook ``field_sales_store_doctypes``)."""
	return list(frappe.get_hooks("field_sales_store_doctypes", app_name="field_sales"))


def order_series(store: str | None) -> str | None:
	"""
	Field Order name series on the shared site (D4: per-store numbers, ``FO-ACME-2026-00001``);
	``None`` on a single-store site, which keeps its ``naming_series`` (``FO-2026-00001``).
	"""
	if not is_multi_store() or not store:
		return None
	return f"FO-{store}-.YYYY.-.#####"


def invoice_series(store: str | None, default: str) -> str:
	"""
	Invoice number series: per store on the shared site (``ACME/26/00001``: GST allows at most
	16 characters, and each business needs one unbroken series), else ``default``.
	"""
	if not is_multi_store() or not store:
		return default
	return f"{store}/.YY./.#####"


def store_filters(store: str | None = None) -> dict:
	"""``filters`` for ``frappe.get_all`` / ``get_list`` limiting a query to one store (default: current)."""
	return {"store": get_store_name(store, required=True)}


def store_criterion(table, store: str | None = None):
	"""The same as a ``frappe.qb`` condition on ``table`` (a ``frappe.qb.DocType`` with a store link)."""
	return table.store == get_store_name(store, required=True)
