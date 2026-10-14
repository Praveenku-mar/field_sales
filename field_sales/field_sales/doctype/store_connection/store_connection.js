// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.ui.form.on("Store Connection", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () =>
			save_if_dirty(frm).then(() => test_connection(frm))
		);
		frm.add_custom_button(__("Fetch Locations"), () =>
			save_if_dirty(frm).then(() => fetch_locations(frm))
		);
	},
});

// The server reads saved settings, so unsaved edits must be saved first.
function save_if_dirty(frm) {
	return frm.is_dirty() ? frm.save() : Promise.resolve();
}

function test_connection(frm) {
	frappe
		.call({
			method: "field_sales.api.test_connection",
			freeze: true,
			freeze_message: __("Testing connection..."),
		})
		.then(({ message: result }) => {
			frappe.msgprint({
				title: result.ok ? __("Connection Successful") : __("Connection Failed"),
				indicator: result.ok ? "green" : "red",
				message: result.ok
					? __("Shop: {0} ({1})", [
							frappe.utils.escape_html(result.shop_name),
							frappe.utils.escape_html(result.shop_domain || ""),
					  ]) +
					  "<br>" +
					  __("Scopes: {0}", [
							frappe.utils.escape_html((result.scopes || []).join(", ") || __("none reported")),
					  ])
					: frappe.utils.escape_html(result.message),
			});
			frm.reload_doc();
		});
}

function fetch_locations(frm) {
	frappe
		.call({
			method: "field_sales.api.get_locations",
			type: "GET",
			freeze: true,
			freeze_message: __("Fetching locations..."),
		})
		.then(({ message: locations }) => {
			if (!locations || !locations.length) {
				frappe.msgprint(__("No active locations found on the store."));
				return;
			}

			const dialog = new frappe.ui.Dialog({
				title: __("Select Default Location"),
				fields: [
					{
						fieldname: "location",
						fieldtype: "Select",
						label: __("Location"),
						reqd: 1,
						options: locations.map((l) => ({ label: l.name, value: l.id })),
						default: frm.doc.default_location_id,
					},
				],
				primary_action_label: __("Set Default"),
				primary_action({ location }) {
					const picked = locations.find((l) => l.id === location);
					merge_locations(frm, locations, picked.id);
					frm.set_value({
						default_location_id: picked.id,
						default_location_name: picked.name,
					})
						.then(() => frm.save())
						.then(() => dialog.hide());
				},
			});
			dialog.show();
		});
}

// Add the store's locations to the Locations table. New ones start unticked (the
// default is ticked), names are refreshed and existing ticks are kept. Rows the
// store no longer returns are left alone for you to untick or delete.
function merge_locations(frm, locations, default_id) {
	locations.forEach((loc) => {
		const row = (frm.doc.locations || []).find((r) => r.location_id === loc.id);
		if (row) {
			frappe.model.set_value(row.doctype, row.name, "location_name", loc.name);
		} else {
			frm.add_child("locations", {
				location_id: loc.id,
				location_name: loc.name,
				sell_from: loc.id === default_id ? 1 : 0,
			});
		}
	});
	frm.refresh_field("locations");
}
