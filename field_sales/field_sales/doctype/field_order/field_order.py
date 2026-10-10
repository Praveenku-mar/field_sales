# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from field_sales.sync.orders import enqueue_push_order


class FieldOrder(Document):
	# ------------------------------------------------------------------
	# Frappe hooks
	# ------------------------------------------------------------------

	def validate(self):
		self._validate_item_quantities()
		self._validate_no_duplicate_products()
		self._compute_item_amounts()
		self._compute_totals()

	def before_submit(self):
		self._block_on_stock_warning()

	def on_submit(self):
		self.db_set("sync_status", "Pending")
		if self.visit:
			frappe.db.set_value(
				"Visit", self.visit, "order_taken", 1, update_modified=False
			)
		enqueue_push_order(self.name)

	def on_cancel(self):
		self.db_set("sync_status", "Draft")

	# ------------------------------------------------------------------
	# Validation helpers
	# ------------------------------------------------------------------

	def _validate_item_quantities(self):
		"""Every line item must have qty > 0."""
		for row in self.items:
			if flt(row.qty) <= 0:
				frappe.throw(
					_("Row {0}: Quantity must be greater than 0.").format(row.idx)
				)

	def _validate_no_duplicate_products(self):
		"""The same Platform Product variant must not appear twice."""
		seen = {}
		for row in self.items:
			if not row.product:
				continue
			if row.product in seen:
				frappe.throw(
					_(
						"Row {0}: Product <b>{1}</b> is already present in Row {2}. "
						"Merge the quantities instead of adding a duplicate line."
					).format(row.idx, row.product_title or row.product, seen[row.product])
				)
			seen[row.product] = row.idx

	def _block_on_stock_warning(self):
		"""Prevent submission when any item exceeds tracked inventory."""
		for row in self.items:
			if row.stock_warning:
				frappe.throw(
					_(
						"Row {0}: <b>{1}</b> exceeds available stock — "
						"only {2} unit(s) available."
					).format(
						row.idx,
						row.product_title or row.product,
						flt(row.available_qty),
					)
				)

	# ------------------------------------------------------------------
	# Computation helpers
	# ------------------------------------------------------------------

	def _compute_item_amounts(self):
		"""
		For every child row:
		  - amount = qty × rate
		  - stock_warning = 1  iff  track_inventory is set on the product
		                            AND qty > available_qty

		Fetches track_inventory for all products in a single query to
		avoid N+1 DB hits.
		"""
		products = [row.product for row in self.items if row.product]

		track_map = {}
		if products:
			track_map = {
				r.name: r.track_inventory
				for r in frappe.get_all(
					"Platform Product",
					filters={"name": ["in", products]},
					fields=["name", "track_inventory"],
				)
			}

		for row in self.items:
			row.amount = flt(flt(row.qty) * flt(row.rate), row.precision("amount"))

			tracks = track_map.get(row.product, 0)
			row.stock_warning = (
				1 if tracks and flt(row.qty) > flt(row.available_qty) else 0
			)

	def _compute_totals(self):
		"""
		Roll up item amounts into order-level totals, rounded to currency
		precision so they match what the store calculates from the same order.
		"""
		self.total_qty = sum(flt(r.qty) for r in self.items)
		self.net_total = flt(sum(flt(r.amount) for r in self.items), self.precision("net_total"))
		self.tax_amount = flt(
			flt(self.net_total) * flt(self.tax_percent) / 100, self.precision("tax_amount")
		)
		self.grand_total = flt(
			flt(self.net_total) + flt(self.tax_amount) - flt(self.discount_amount),
			self.precision("grand_total"),
		)

