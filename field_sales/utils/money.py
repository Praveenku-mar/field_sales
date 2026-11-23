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


def fill_currency(doc, method: str | None = None) -> None:
	"""``before_insert`` (after ``stamp_store``) of Field Order and Platform Product: their currency."""
	if not doc.get("currency"):
		doc.currency = store_currency(doc.get("store"))
