// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.listview_settings["Visit"] = {
	// Guarantee status is always present in the list payload.
	add_fields: ["status"],

	get_indicator: function (doc) {
		const indicators = {
			"Planned":     [__("Planned"),     "gray",   "status,=,Planned"],
			"In Progress": [__("In Progress"), "orange", "status,=,In Progress"],
			"Completed":   [__("Completed"),   "green",  "status,=,Completed"],
			"Cancelled":   [__("Cancelled"),   "red",    "status,=,Cancelled"],
		};

		return indicators[doc.status] || indicators["Planned"];
	},
};
