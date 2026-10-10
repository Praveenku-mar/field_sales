// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

// ---------------------------------------------------------------------------
// Parent form handlers
// ---------------------------------------------------------------------------

frappe.ui.form.on("Field Order", {
	setup: function (frm) {
		// Filter Visit dropdown to the currently selected Retailer.
		// set_query uses a closure so it always reads the live frm.doc value.
		frm.set_query("visit", function () {
			return {
				filters: { retailer: frm.doc.retailer || "__none__" },
			};
		});
	},

	refresh: function (frm) {
		_highlight_stock_warnings(frm);
	},

	retailer: function (frm) {
		// Clear a visit that may belong to the old retailer
		if (frm.doc.visit) {
			frm.set_value("visit", "");
		}

		if (!frm.doc.retailer) {
			frm.set_value("sales_rep", "");
			return;
		}

		// Auto-fill Sales Rep from the Retailer record
		frappe.db.get_value(
			"Retailer",
			frm.doc.retailer,
			"sales_rep",
			function (r) {
				if (r && r.sales_rep) {
					frm.set_value("sales_rep", r.sales_rep);
				}
			}
		);
	},

	tax_percent: function (frm) {
		_recalc_totals(frm);
	},

	discount_amount: function (frm) {
		_recalc_totals(frm);
	},
});

// ---------------------------------------------------------------------------
// Child table handlers
// ---------------------------------------------------------------------------

frappe.ui.form.on("Field Order Item", {
	qty: function (frm, cdt, cdn) {
		_recalc_row(frm, cdt, cdn);
	},

	rate: function (frm, cdt, cdn) {
		_recalc_row(frm, cdt, cdn);
	},

	items_remove: function (frm) {
		_recalc_totals(frm);
		_highlight_stock_warnings(frm);
	},
});

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/**
 * Recompute amount for a single child row, then roll up parent totals.
 */
function _recalc_row(frm, cdt, cdn) {
	var row = locals[cdt][cdn];
	var amount = flt(row.qty) * flt(row.rate);
	frappe.model.set_value(cdt, cdn, "amount", amount).then(function () {
		_recalc_totals(frm);
		_highlight_stock_warnings(frm);
	});
}

/**
 * Recompute order-level totals in the browser so the user sees live values
 * without needing to save.
 */
function _recalc_totals(frm) {
	var total_qty = 0;
	var net_total = 0;

	(frm.doc.items || []).forEach(function (row) {
		total_qty += flt(row.qty);
		net_total += flt(row.amount);
	});

	var tax_amount = net_total * flt(frm.doc.tax_percent) / 100;
	var grand_total = net_total + tax_amount - flt(frm.doc.discount_amount);

	frm.set_value("total_qty", total_qty);
	frm.set_value("net_total", net_total);
	frm.set_value("tax_amount", tax_amount);
	frm.set_value("grand_total", grand_total);
}

/**
 * Paint child rows red when stock_warning is set, clear it otherwise.
 * Called after every qty/rate change and on form refresh.
 */
function _highlight_stock_warnings(frm) {
	var grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) return;

	grid.grid_rows.forEach(function (grid_row) {
		var doc = grid_row.doc;
		if (doc && doc.stock_warning) {
			$(grid_row.row).css({
				"background-color": "#fde8e8",
				"color": "#c0392b",
			});
		} else {
			$(grid_row.row).css({
				"background-color": "",
				"color": "",
			});
		}
	});
}
