// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.listview_settings["Retailer"] = {
	// Ensure sync_status is always available in the list response even if the
	// column is later removed from in_list_view in the DocType definition.
	add_fields: ["sync_status"],

	onload(listview) {
		listview.page.add_actions_menu_item(__("Sync to Store"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) return;

			frappe
				.call({ method: "field_sales.api.sync_retailers", args: { names }, freeze: true })
				.then(({ message: r }) => {
					frappe.show_alert({ message: r.message, indicator: "blue" });
					listview.refresh();
				});
		});
	},

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
