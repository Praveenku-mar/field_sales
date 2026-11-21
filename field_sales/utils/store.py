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

import re
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _

STORE_DOCTYPE = "Store"
_LOCAL_KEY = "field_sales_store"
# The site's only Store, cached for the request/job (cleared when a Store is added or removed).
_ONLY_KEY = "field_sales_only_store"
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


def clear_store_cache() -> None:
	"""Forget the cached "only Store" (called when a Store is inserted, renamed or deleted)."""
	setattr(frappe.local, _ONLY_KEY, None)


def get_store_name(store: str | None = None, required: bool = False) -> str | None:
	"""
	The store to work for: ``store``, else the current store, else the site's only Store.

	:param required: Raise when there is no Store at all (else return ``None``).
	:raises NoStoreContext: Several stores exist and none was given or set.
	"""
	name = store or current_store()
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
	"""``before_insert`` for store-linked doctypes: fill ``store`` from the context when empty."""
	if not doc.get("store"):
		doc.store = get_store_name()


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
