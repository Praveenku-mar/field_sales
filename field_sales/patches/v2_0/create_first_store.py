# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Per-store settings move from the Store Connection single to a Store record.

- Today's Store Connection values become the site's first Store (code from the shop name),
  with its encrypted tokens / secret and its Store Location rows.
- Every record of the store-linked doctypes (hook ``field_sales_store_doctypes``) gets that store.
- Sync Logs that pointed at Store Connection point at the Store.
- The moved values are removed from Store Connection, which keeps only the vendor-wide fields.

Safe to run again: with a Store already there it only fills records still without a store.
Also run by ``install.after_install`` (a fresh install marks patches done without running them).
"""

import frappe
from frappe.utils.data import cast
from frappe.utils.password import get_decrypted_password, set_encrypted_password

from field_sales.utils.store import is_multi_store, make_store_code, store_doctypes

SOURCE = "Store Connection"
VENDOR_FIELDS = ("failed_log_retention_days", "plan_limits")
PASSWORD_FIELDS = ("shopify_client_secret", "access_token", "refresh_token")
# Records given their store per UPDATE (and commit) by _fill_store_links.
BATCH_SIZE = 10_000


def execute():
	frappe.reload_doc("field_sales", "doctype", "store")
	frappe.reload_doc("field_sales", "doctype", "store_location")

	stores = frappe.get_all("Store", pluck="name", order_by="creation asc")
	if not stores:
		store = _create_store_from_connection()
		if not store:
			return
	elif len(stores) == 1:
		store = stores[0]
	else:
		return  # a multi-store site: nothing to guess

	_fill_store_links(store)


def _create_store_from_connection() -> str | None:
	values = dict(frappe.db.sql("select field, value from `tabSingles` where doctype = %s", SOURCE))
	if is_multi_store() and not values.get("store_url"):
		return None  # the shared site: stores are created by each shop's install

	meta = frappe.get_meta("Store")
	doc = frappe.new_doc("Store")
	for df in meta.fields:
		if df.fieldtype in ("Table", "Password") or df.fieldname not in values:
			continue
		doc.set(df.fieldname, cast(df.fieldtype, values[df.fieldname]))
	doc.store_code = make_store_code(values.get("shop_name") or values.get("store_url") or "MAIN")
	doc.name = doc.store_code
	doc.tax_mode = doc.tax_mode or "GST"
	doc.platform = doc.platform or "Shopify"
	# Straight insert: the values were valid on Store Connection, and the controller would queue a
	# shop settings sync or reject an old Store URL format mid-migration.
	doc.db_insert()

	for field in PASSWORD_FIELDS:
		secret = get_decrypted_password(SOURCE, SOURCE, field, raise_exception=False)
		if secret:
			set_encrypted_password("Store", doc.name, secret, field)
			frappe.db.set_value("Store", doc.name, field, "*" * len(secret), update_modified=False)

	frappe.db.sql(
		"""update `tabStore Location` set parent = %s, parenttype = 'Store', parentfield = 'locations'
		where parenttype = %s""",
		(doc.name, SOURCE),
	)
	frappe.db.sql(
		"""update `tabSync Log` set reference_doctype = 'Store', reference_name = %s
		where reference_doctype = %s""",
		(doc.name, SOURCE),
	)

	frappe.db.sql(
		f"""delete from `tabSingles` where doctype = %s
		and field not in ({", ".join(["%s"] * len(VENDOR_FIELDS))}, 'name', 'modified', 'modified_by', 'creation', 'owner')""",
		(SOURCE, *VENDOR_FIELDS),
	)
	frappe.db.sql("delete from `__Auth` where doctype = %s", SOURCE)
	frappe.clear_cache(doctype=SOURCE)
	return doc.name


def _fill_store_links(store: str) -> None:
	for doctype in store_doctypes():
		if not frappe.db.has_column(doctype, "store"):
			frappe.reload_doc("field_sales", "doctype", frappe.scrub(doctype))
		_fill_doctype(doctype, store)


def _fill_doctype(doctype: str, store: str) -> None:
	"""
	Give ``doctype``'s records without a store ``store``, ``BATCH_SIZE`` rows at a time with a
	commit after each: one ``UPDATE`` over years of Sync Logs or Product Stock would lock the
	table for the whole migrate. Picked by name (``store`` is indexed), so it also runs on Postgres.
	"""
	table = frappe.qb.DocType(doctype)
	no_store = table.store.isnull() | (table.store == "")
	while names := (
		frappe.qb.from_(table).select(table.name).where(no_store).limit(BATCH_SIZE).run(pluck=True)
	):
		frappe.qb.update(table).set(table.store, store).where(table.name.isin(names)).run()
		frappe.db.commit()
