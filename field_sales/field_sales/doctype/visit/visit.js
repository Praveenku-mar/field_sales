// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

const DEFAULT_GEOFENCE_RADIUS_M = 50;

const GPS_OPTIONS = { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 };

frappe.ui.form.on("Visit", {
	onload(frm) {
		if (frm.is_new()) {
			set_new_visit_defaults(frm);
		}
	},

	refresh(frm) {
		add_check_in_buttons(frm);
		show_geofence_indicator(frm);
		render_location_map(frm);
	},
});

// ----------------------------------------------------------------------
// New visit defaults (also used by the workspace "New Visit" shortcut)
// ----------------------------------------------------------------------

function set_new_visit_defaults(frm) {
	if (!frm.doc.visit_date) {
		frm.set_value("visit_date", frappe.datetime.get_today());
	}
	if (frm.doc.sales_rep) return;

	frappe.db
		.get_value("Sales Rep", { user: frappe.session.user, active: 1 }, "name")
		.then(({ message }) => {
			// The user may have picked a rep while the lookup was running.
			if (message && message.name && !frm.doc.sales_rep) {
				frm.set_value("sales_rep", message.name);
			}
		});
}

// ----------------------------------------------------------------------
// Check In / Check Out
// ----------------------------------------------------------------------

function add_check_in_buttons(frm) {
	if (frm.is_new()) return;

	if (frm.doc.status === "Planned") {
		frm.add_custom_button(__("Check In"), () =>
			check_in_or_out(frm, "field_sales.api.check_in", __("Checking in…"))
		).addClass("btn-primary");
	}

	if (frm.doc.status === "In Progress") {
		frm.add_custom_button(__("Check Out"), () =>
			check_in_or_out(frm, "field_sales.api.check_out", __("Checking out…"))
		).addClass("btn-primary");

		frm.add_custom_button(__("New Field Order"), () =>
			frappe.new_doc("Field Order", {
				retailer: frm.doc.retailer,
				visit: frm.doc.name,
				sales_rep: frm.doc.sales_rep,
			})
		);
	}
}

function check_in_or_out(frm, method, freeze_message) {
	// Save pending edits (e.g. notes) first, so reload_doc does not discard them.
	const saved = frm.is_dirty() ? frm.save() : Promise.resolve();

	saved
		.then(() => get_current_position())
		.then((coords) =>
			frappe.call({
				method,
				args: {
					visit: frm.doc.name,
					latitude: coords.latitude,
					longitude: coords.longitude,
					accuracy: coords.accuracy,
				},
				freeze: true,
				freeze_message,
			})
		)
		.then(({ message: r }) => {
			if (r.warning) {
				frappe.msgprint({ title: __("Outside Geofence"), message: r.warning, indicator: "orange" });
			} else if (r.retailer_location_set) {
				frappe.show_alert({
					message: __("Retailer location saved from this check-in"),
					indicator: "green",
				});
			} else {
				frappe.show_alert({ message: __("Visit {0}", [__(r.status)]), indicator: "green" });
			}
			return frm.reload_doc();
		})
		.catch((err) => {
			// Server errors are already shown by frappe.call; only report location failures here.
			if (err && err.is_location_error) {
				frappe.msgprint({ title: __("Location Unavailable"), message: err.message, indicator: "red" });
			}
		});
}

function get_current_position() {
	return new Promise((resolve, reject) => {
		const fail = (message) => reject({ is_location_error: true, message });
		const enable_msg = __("Allow location access in your browser and use HTTPS.");

		if (!window.isSecureContext || !navigator.geolocation) {
			return fail(__("Location is not available on this page.") + " " + enable_msg);
		}

		frappe.dom.freeze(__("Getting your location…"));
		navigator.geolocation.getCurrentPosition(
			(position) => {
				frappe.dom.unfreeze();
				resolve(position.coords);
			},
			(error) => {
				frappe.dom.unfreeze();
				if (error.code === error.PERMISSION_DENIED) {
					fail(__("Location permission was denied.") + " " + enable_msg);
				} else if (error.code === error.TIMEOUT) {
					fail(__("Could not get a GPS fix within 15 seconds. Move to an open area and try again."));
				} else {
					fail(__("Your location is unavailable.") + " " + enable_msg);
				}
			},
			GPS_OPTIONS
		);
	});
}

// ----------------------------------------------------------------------
// Geofence indicator and map
// ----------------------------------------------------------------------

function has_coords(latitude, longitude) {
	// (0, 0) is treated as "not set": it is never a real location.
	return Boolean(flt(latitude) || flt(longitude));
}

function get_geofence_radius(frm) {
	return frappe.db
		.get_value("Retailer", frm.doc.retailer, "geofence_radius_m")
		.then(({ message }) => (message && cint(message.geofence_radius_m)) || DEFAULT_GEOFENCE_RADIUS_M);
}

function show_geofence_indicator(frm) {
	if (!frm.doc.check_in_time || !has_coords(frm.doc.retailer_latitude, frm.doc.retailer_longitude)) {
		return;
	}

	get_geofence_radius(frm).then((radius) => {
		if (frm.doc.within_geofence) {
			frm.dashboard.add_indicator(__("Within {0} m", [radius]), "green");
		} else {
			frm.dashboard.add_indicator(
				__("{0} m away", [Math.round(flt(frm.doc.distance_from_retailer_m))]),
				"orange"
			);
		}
	});
}

function render_location_map(frm) {
	const field = frm.get_field("location_map_html");
	if (frm._visit_map) {
		frm._visit_map.remove();
		frm._visit_map = null;
	}
	field.$wrapper.empty();

	const has_check_in = has_coords(frm.doc.latitude, frm.doc.longitude);
	const has_retailer = has_coords(frm.doc.retailer_latitude, frm.doc.retailer_longitude);
	if (!frm.doc.check_in_time || !(has_check_in || has_retailer)) return;

	const container = $(`<div style="height: 300px; border-radius: var(--border-radius-md);"></div>`).appendTo(
		field.$wrapper
	);

	Promise.all([
		frappe.require("leaflet.bundle.js"),
		frappe.require("leaflet.bundle.css"),
		has_retailer ? get_geofence_radius(frm) : null,
	]).then(([, , radius]) => {
		// The form may have been refreshed again while Leaflet loaded.
		if (!document.body.contains(container[0])) return;

		const defaults = frappe.utils.map_defaults;
		L.Icon.Default.imagePath = defaults.image_path;

		const map = L.map(container[0], { scrollWheelZoom: false });
		L.tileLayer(defaults.tiles.default_tile.url, defaults.tiles.default_tile.options).addTo(map);
		frm._visit_map = map;

		const points = [];
		if (has_retailer) {
			const shop = [flt(frm.doc.retailer_latitude), flt(frm.doc.retailer_longitude)];
			L.marker(shop).addTo(map).bindTooltip(__("Retailer: {0}", [frm.doc.retailer_name || frm.doc.retailer]));
			L.circle(shop, { radius, color: "#2490ef", weight: 1, fillOpacity: 0.08 }).addTo(map);
			points.push(shop);
		}
		if (has_check_in) {
			const here = [flt(frm.doc.latitude), flt(frm.doc.longitude)];
			L.circleMarker(here, {
				radius: 8,
				color: "#fff",
				weight: 2,
				fillColor: frm.doc.within_geofence ? "#29cd42" : "#f8814f",
				fillOpacity: 1,
			})
				.addTo(map)
				.bindTooltip(__("Check-in (±{0} m)", [Math.round(flt(frm.doc.location_accuracy_m))]));
			points.push(here);
		}

		if (points.length > 1) {
			map.fitBounds(points, { padding: [40, 40], maxZoom: 18 });
		} else {
			map.setView(points[0], 17);
		}
		// The section may have just become visible; let Leaflet measure it.
		setTimeout(() => map.invalidateSize(), 100);
	});
}
