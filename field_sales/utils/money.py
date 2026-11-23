import frappe

from field_sales.utils.store import NoStoreContext, get_store_value, is_multi_store


def site_currency() -> str:
	"""The site's default currency (System Settings), which follows the store's on single-store sites."""
	return frappe.db.get_default("currency") or "INR"


def store_currency(store: str | None = None) -> str:
	"""
	The currency new amounts of ``store`` (default: the current store) are in.

	On the shared multi-store site, the store's ``shop_currency`` (filled from the shop); else, and
	while a store has none yet, the site's default currency, as before.
	"""
	if is_multi_store():
		try:
			currency = get_store_value("shop_currency", store=store)
		except NoStoreContext:
			currency = None
		if currency:
			return currency
	return site_currency()


def record_currency(doc) -> str:
	"""The currency of an existing record's amounts: its own ``currency``, else its store's."""
	return doc.get("currency") or store_currency(doc.get("store"))


# Records that carry their store's currency (filled on insert, and backfilled by fill_store_currency).
PRICED_DOCTYPES = ("Field Order", "Platform Product")


def fill_currency(doc, method: str | None = None) -> None:
	"""
	``before_insert`` (after ``stamp_store``) of Field Order and Platform Product: their currency.

	On the shared site a new store's currency is not known until its first shop settings sync;
	until then the record is left without one (never the site's, which would stay wrong), and
	``fill_store_currency`` gives it the store's once that sync saves it.
	"""
	if doc.get("currency"):
		return
	if is_multi_store():
		try:
			doc.currency = get_store_value("shop_currency", store=doc.get("store")) or None
		except NoStoreContext:
			doc.currency = None
		return
	doc.currency = site_currency()


def fill_store_currency(store: str, currency: str) -> None:
	"""Give ``store``'s records still without a currency (made before it was known) ``currency``."""
	for doctype in PRICED_DOCTYPES:
		frappe.db.sql(
			f"""update `tab{doctype}` set currency = %(currency)s
			where store = %(store)s and (currency is null or currency = '')""",
			{"currency": currency, "store": store},
		)
