// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.listview_settings["Field Order"] = {
	add_fields: ["sync_status", "docstatus", "sync_error"],

	onload(listview) {
		listview.page.add_actions_menu_item(__("Retry Sync"), () => {
			const failed = listview
				.get_checked_items()
				.filter((doc) => doc.docstatus === 1 && doc.sync_status === "Failed");
			if (!failed.length) {
				frappe.msgprint(__("Select submitted orders whose sync failed."));
				return;
			}
			Promise.all(
				failed.map((doc) =>
					frappe.call({ method: "field_sales.api.retry_sync", args: { field_order: doc.name } })
				)
			).then(() => {
				frappe.show_alert({ message: __("Retry queued for {0} order(s)", [failed.length]), indicator: "blue" });
				listview.refresh();
			});
		});
	},

	get_indicator(doc) {
		if (doc.docstatus === 0) return [__("Draft"), "gray", "docstatus,=,0"];
		if (doc.docstatus === 2) return [__("Cancelled"), "red", "docstatus,=,2"];
		const indicators = {
			Synced: [__("Synced"), "green", "sync_status,=,Synced"],
			Pending: [__("Sync Pending"), "orange", "sync_status,=,Pending"],
			Failed: [__("Sync Failed"), "red", "sync_status,=,Failed"],
		};
		return indicators[doc.sync_status] || [__("Submitted"), "blue", "docstatus,=,1"];
	},

	button: {
		show: (doc) => doc.docstatus === 1 && doc.sync_status === "Failed",
		get_label: () => __("Retry Sync"),
		get_description: (doc) => doc.sync_error || __("Retry sync"),
		action: (doc) =>
			frappe
				.call({ method: "field_sales.api.retry_sync", args: { field_order: doc.name } })
				.then(({ message: r }) => {
					frappe.show_alert({ message: r.message, indicator: "blue" });
					cur_list && cur_list.refresh();
				}),
	},
};
