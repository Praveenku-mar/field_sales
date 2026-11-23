// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.ui.form.on("Commission Entry", {
	onload(frm) {
		// No "Today" default in the DocType (the server fills the store's date: fill_store_dates);
		// Desk shows today in the user's time zone, which is the store's on the shared site.
		if (frm.is_new() && !frm.doc.posting_date) {
			frm.set_value("posting_date", frappe.datetime.get_today());
		}
	},
});
