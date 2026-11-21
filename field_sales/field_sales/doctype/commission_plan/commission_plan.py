# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CommissionPlan(Document):
	def autoname(self):
		"""
		The plan's name is its ``plan_name`` while that is free; a second store's plan of the same
		name becomes ``<plan_name> (<store>)``. Links show the title (``plan_name``) either way.
		"""
		plan_name = (self.plan_name or "").strip()
		self.name = (
			plan_name if not frappe.db.exists("Commission Plan", plan_name) else f"{plan_name} ({self.store})"
		)

	def validate(self):
		self._validate_unique_name()
		self._validate_penalty_factor()
		self._validate_rule()

	def _validate_unique_name(self):
		self.plan_name = (self.plan_name or "").strip()
		if frappe.db.exists(
			"Commission Plan", {"store": self.store, "plan_name": self.plan_name, "name": ("!=", self.name)}
		):
			frappe.throw(_("A plan named {0} already exists in this store.").format(self.plan_name))

	def _validate_penalty_factor(self):
		if not 0 <= flt(self.penalty_factor) <= 1:
			frappe.throw(_("Penalty Factor must be between 0 and 1."))
		if flt(self.override_levels) < 0:
			frappe.throw(_("Override Levels cannot be negative."))

	def _validate_rule(self):
		if self.rule_type == "Flat %":
			if flt(self.flat_rate) <= 0:
				frappe.throw(_("Flat Rate % is required for a Flat % plan."))
		elif self.rule_type == "Product Rate":
			if not self.rates and flt(self.default_rate) <= 0:
				frappe.throw(_("Add at least one rate row or set a Default Rate % for a Product Rate plan."))
			for row in self.rates:
				if row.match_type == "Product" and not row.product:
					frappe.throw(_("Row {0}: Product is required.").format(row.idx))
				if row.match_type != "Product" and not (row.match_value or "").strip():
					frappe.throw(_("Row {0}: Match Value is required.").format(row.idx))
		elif self.rule_type == "Tiered Slab":
			self._validate_slabs()
		elif self.rule_type == "Per Order":
			if flt(self.per_order_amount) <= 0:
				frappe.throw(_("Amount per Order is required for a Per Order plan."))

	def _validate_slabs(self):
		if not self.slabs:
			frappe.throw(_("Add at least one slab for a Tiered Slab plan."))

		previous_to = None
		for row in self.slabs:
			if flt(row.from_amount) < 0:
				frappe.throw(_("Slab row {0}: From Amount cannot be negative.").format(row.idx))
			if flt(row.to_amount) and flt(row.to_amount) <= flt(row.from_amount):
				frappe.throw(
					_("Slab row {0}: To Amount must be greater than From Amount (or 0 for no limit).").format(
						row.idx
					)
				)
			if previous_to is not None:
				if previous_to == 0:
					frappe.throw(
						_(
							"Slab row {0}: the previous slab has no upper limit, so it must be the last one."
						).format(row.idx)
					)
				if flt(row.from_amount) < previous_to:
					frappe.throw(
						_("Slab row {0}: slabs must be sorted by From Amount and must not overlap.").format(
							row.idx
						)
					)
			previous_to = flt(row.to_amount)


def on_doctype_update():
	# Plan names are unique inside a store; two stores may both have a "Standard" plan.
	frappe.db.add_unique("Commission Plan", ["store", "plan_name"], constraint_name="unique_store_plan_name")
