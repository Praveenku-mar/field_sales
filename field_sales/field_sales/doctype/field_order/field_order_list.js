// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.listview_settings["Field Order"] = {
	// Ensure sync_status is always present in the list payload.
	add_fields: ["sync_status"],

	get_indicator: function (doc) {
		const indicators = {
			Synced:  [__("Synced"),  "green",  "sync_status,=,Synced"],
			Pending: [__("Pending"), "orange", "sync_status,=,Pending"],
			Failed:  [__("Failed"),  "red",    "sync_status,=,Failed"],
			Draft:   [__("Draft"),   "gray",   "sync_status,=,Draft"],
		};

		return indicators[doc.sync_status] || indicators["Draft"];
	},
};
