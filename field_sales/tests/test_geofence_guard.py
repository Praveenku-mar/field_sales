# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from field_sales.api import check_in, check_out

# The shop, and a point about 1.1 km north of it (outside the 50 m geofence).
SHOP_LAT, SHOP_LNG = 11.0168, 76.9558
AWAY_LAT, AWAY_LNG = 11.0268, 76.9558


class TestGeofenceGuard(IntegrationTestCase):
	"""Reps cannot move the goalposts of the check-in geofence."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.manager_user = self.make_user("geoguard.manager@example.com", ["Field Sales Rep", "Sales Manager"])
		self.manager_rep = self.make_rep(self.manager_user)
		self.rep_user = self.make_user("geoguard.rep@example.com", ["Field Sales Rep"])
		self.rep = self.make_rep(self.rep_user, reports_to=self.manager_rep.name)
		self.sysman = self.make_user("geoguard.sysman@example.com", ["System Manager"])
		self.retailer = self.make_retailer(latitude=SHOP_LAT, longitude=SHOP_LNG)

	def tearDown(self):
		frappe.set_user("Administrator")
		# IntegrationTestCase only rolls back once per class.
		frappe.db.after_rollback.reset()
		frappe.db.rollback()

	# -- helpers -----------------------------------------------------------

	def make_user(self, email, roles):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		user.add_roles(*roles)
		return user.name

	def make_rep(self, user, reports_to=None):
		return frappe.get_doc({"doctype": "Sales Rep", "user": user, "reports_to": reports_to}).insert(
			ignore_permissions=True
		)

	def make_retailer(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Retailer",
				"retailer_name": "Geofence Stores",
				"phone": "9876543210",
				"address_line1": "1 Main Road",
				"city": "Coimbatore",
				"sales_rep": self.rep.name,
				**values,
			}
		).insert(ignore_permissions=True)

	def make_visit(self, retailer=None):
		return frappe.get_doc(
			{
				"doctype": "Visit",
				"sales_rep": self.rep.name,
				"retailer": (retailer or self.retailer).name,
				"visit_date": nowdate(),
				"status": "Planned",
			}
		).insert(ignore_permissions=True)

	def as_user(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def set_retailer(self, user, **values):
		return self.as_user(user, frappe.client.set_value, "Retailer", self.retailer.name, values)

	def retailer_value(self, fieldname, name=None):
		return frappe.db.get_value("Retailer", name or self.retailer.name, fieldname)

	def completed_visit_away_from_shop(self):
		visit = self.make_visit()
		self.as_user(self.rep_user, check_in, visit.name, AWAY_LAT, AWAY_LNG, 10)
		self.as_user(self.rep_user, check_out, visit.name, AWAY_LAT, AWAY_LNG, 10, no_order_reason="Owner Absent")
		return frappe.get_doc("Visit", visit.name)

	def save_visit_at_rep_position(self, user, visit):
		doc = frappe.get_doc("Visit", visit.name).as_dict()
		doc.update(retailer_latitude=AWAY_LAT, retailer_longitude=AWAY_LNG)
		self.as_user(user, frappe.client.save, doc)
		return frappe.get_doc("Visit", visit.name)

	# -- retailer location -------------------------------------------------

	def test_rep_cannot_move_located_retailer(self):
		with self.assertRaises(frappe.PermissionError):
			self.set_retailer(self.rep_user, latitude=AWAY_LAT, longitude=AWAY_LNG)
		self.assertEqual(flt(self.retailer_value("latitude")), SHOP_LAT)

	def test_rep_can_set_first_location(self):
		unlocated = self.make_retailer()
		self.as_user(self.rep_user, frappe.client.set_value, "Retailer", unlocated.name, {"latitude": SHOP_LAT, "longitude": SHOP_LNG})
		self.assertEqual(flt(self.retailer_value("latitude", unlocated.name)), SHOP_LAT)

	def test_rep_can_edit_other_retailer_fields(self):
		self.set_retailer(self.rep_user, address_line2="Near the bus stand")
		self.assertEqual(self.retailer_value("address_line2"), "Near the bus stand")

	def test_first_check_in_still_sets_location(self):
		unlocated = self.make_retailer()
		visit = self.make_visit(unlocated)
		self.as_user(self.rep_user, check_in, visit.name, SHOP_LAT, SHOP_LNG, 10)
		self.assertEqual(flt(self.retailer_value("latitude", unlocated.name)), SHOP_LAT)
		self.assertEqual(frappe.db.get_value("Visit", visit.name, "status"), "In Progress")

	def test_manager_can_move_retailer(self):
		self.set_retailer(self.manager_user, latitude=AWAY_LAT, longitude=AWAY_LNG)
		self.assertEqual(flt(self.retailer_value("latitude")), AWAY_LAT)

	# -- radius ------------------------------------------------------------

	def test_rep_cannot_change_radius(self):
		self.set_retailer(self.rep_user, geofence_radius_m=5000)
		self.assertEqual(self.retailer_value("geofence_radius_m"), 50)

	def test_rep_new_retailer_gets_default_radius(self):
		retailer = self.as_user(
			self.rep_user,
			frappe.get_doc(
				{
					"doctype": "Retailer",
					"retailer_name": "Wide Stores",
					"phone": "9876543211",
					"address_line1": "2 Main Road",
					"city": "Coimbatore",
					"geofence_radius_m": 5000,
				}
			).insert,
		)
		self.assertEqual(self.retailer_value("geofence_radius_m", retailer.name), 50)

	def test_manager_can_change_radius(self):
		self.set_retailer(self.manager_user, geofence_radius_m=200)
		self.assertEqual(self.retailer_value("geofence_radius_m"), 200)

	# -- visit -------------------------------------------------------------

	def test_rep_cannot_move_retailer_position_on_completed_visit(self):
		visit = self.completed_visit_away_from_shop()
		self.assertGreater(flt(visit.distance_from_retailer_m), 1000)
		self.assertFalse(visit.within_geofence)

		saved = self.save_visit_at_rep_position(self.rep_user, visit)
		self.assertEqual(flt(saved.retailer_latitude), SHOP_LAT)
		self.assertEqual(flt(saved.distance_from_retailer_m), flt(visit.distance_from_retailer_m))
		self.assertFalse(saved.within_geofence)

	def test_sales_manager_cannot_move_retailer_position_on_visit(self):
		# Only the check-in API and System Managers write a checked-in visit's measurement.
		visit = self.completed_visit_away_from_shop()
		saved = self.save_visit_at_rep_position(self.manager_user, visit)
		self.assertEqual(flt(saved.distance_from_retailer_m), flt(visit.distance_from_retailer_m))

	def test_moving_retailer_later_does_not_change_past_visit(self):
		visit = self.completed_visit_away_from_shop()
		self.set_retailer(self.manager_user, latitude=AWAY_LAT, longitude=AWAY_LNG)
		doc = frappe.get_doc("Visit", visit.name).as_dict()
		doc["remarks"] = "Re-saved after the shop moved"
		self.as_user(self.rep_user, frappe.client.save, doc)
		self.assertEqual(flt(frappe.db.get_value("Visit", visit.name, "distance_from_retailer_m")), flt(visit.distance_from_retailer_m))
