// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.query_reports["Rep Performance"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "sales_rep",
			label: __("Sales Rep"),
			fieldtype: "Link",
			options: "Sales Rep",
		},
		{
			fieldname: "territory",
			label: __("Territory"),
			fieldtype: "Autocomplete",
			// Territory is free text on Sales Rep: offer the values in use.
			get_query: () => ({ query: "field_sales.api.get_territories" }),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "failed_syncs" && data.failed_syncs > 0) {
			return `<span style="color: var(--red-500); font-weight: 600">${value}</span>`;
		}
		if (column.fieldname === "outside_geofence" && data.outside_geofence > 0) {
			return `<span style="color: var(--orange-500)">${value}</span>`;
		}
		return value;
	},
};
