// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.query_reports["Rep Commission Summary"] = {
	filters: [
		{
			// Vendor staff pick one store (required on the shared site); a store's users only see theirs.
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Store",
		},
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
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "sales_rep",
			label: __("Sales Rep"),
			fieldtype: "Link",
			options: "Sales Rep",
		},
		{
			fieldname: "manager",
			label: __("Manager"),
			fieldtype: "Link",
			options: "Sales Rep",
		},
		{
			// The store is single; "store" here means the location the order was sold from.
			fieldname: "location_id",
			label: __("Store Location"),
			fieldtype: "Data",
		},
	],
};
