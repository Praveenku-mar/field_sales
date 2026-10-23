app_name = "field_sales"
app_title = "Field Sales"
app_publisher = "Agamnxt Technologies"
app_description = "Field Sales is a Frappe app that lets a brand’s sales reps check in at retail shops with GPS, take orders against live Shopify stock, and sync those orders to Shopify automatically. Managers can track visits, orders and sync status from one dashboard."
app_email = "praveenkumar@agamnxt.com"
app_license = "mit"

# Send non-GET requests for this app's endpoints as native `application/json`
# bodies instead of form-encoded, per-key JSON-stringified values.
use_json_request_body = True

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "field_sales",
# 		"logo": "/assets/field_sales/logo.png",
# 		"title": "Field Sales",
# 		"route": "/field_sales",
# 		"has_permission": "field_sales.api.permission.has_app_permission",
# 	}
# ]

# The dock, the rail down the left of the desk, is a document rather than a hook. Author it in
# Manage Dock on a developer-mode site and press Export to App, and it is written to
# `field_sales/dock/field_sales/field_sales.json` for git to carry. An app that ships none has no
# rail: its sidebar gets a switcher in the header instead.
#
# A companion app, one that extends a host app rather than standing on its own, says so with
# `mount_on` on that same record, and its entries are appended to the host's rail. Mounting keeps
# the companion off the apps screen, so it takes precedence over any add_to_apps_screen above.

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/field_sales/css/field_sales.css"
# app_include_js = "/assets/field_sales/js/field_sales.js"

# include js, css files in header of web template
# web_include_css = "/assets/field_sales/css/field_sales.css"
# web_include_js = "/assets/field_sales/js/field_sales.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "field_sales/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "field_sales/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Setup Wizard
# ------------

# open a fresh site's setup in this app's own UI instead of the desk wizard.
# must be a non-desk route (not under /desk or /app); to customize setup within
# desk, use setup_wizard_stages / setup_wizard_complete instead.
# setup_wizard_url = "/field_sales/setup"

# Field Sales desktop app in the desk's app switcher (shown to users with an app role).
add_to_apps_screen = [
	{
		"name": "field_sales",
		"logo": "/assets/field_sales/images/logo.svg",
		"title": "Field Sales",
		"route": "/fieldsales",
		"has_permission": "field_sales.www.fieldsales.has_app_access",
	}
]

# Desktop app (frontend/, built into public/frontend): every /fieldsales/* path serves www/fieldsales.html.
# /fs is a short alias for the same app; the router picks its base from the URL.
# Called by www/fieldsales.py (core never imports field_sales.shopify): returns boot values
# when the page is opened inside a store's admin, else None.
field_sales_embedded_page = ["field_sales.shopify.embedded.page_context"]

website_route_rules = [
	{"from_route": "/fieldsales/<path:app_path>", "to_route": "fieldsales"},
	{"from_route": "/fs", "to_route": "fieldsales"},
	{"from_route": "/fs/<path:app_path>", "to_route": "fieldsales"},
]

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "field_sales.utils.jinja_methods",
# 	"filters": "field_sales.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "field_sales.install.before_install"
# after_install = "field_sales.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "field_sales.uninstall.before_uninstall"
# after_uninstall = "field_sales.uninstall.after_uninstall"

# Disable / Enable
# ----------------
# Called when this app is logically disabled or re-enabled on a site,
# without uninstalling it. Use this to hide/restore fields this app adds
# to other apps' doctypes.

# before_disable = "field_sales.uninstall.before_disable"
# after_disable = "field_sales.uninstall.after_disable"
# before_enable = "field_sales.install.before_enable"
# after_enable = "field_sales.install.after_enable"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "field_sales.utils.before_app_install"
# after_app_install = "field_sales.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "field_sales.utils.before_app_uninstall"
# after_app_uninstall = "field_sales.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "field_sales.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "field_sales.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "field_sales.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["field_sales.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# Row-level access by Sales Rep hierarchy (see field_sales/permissions.py).

permission_query_conditions = {
	"Visit": "field_sales.permissions.visit_query",
	"Sales Rep": "field_sales.permissions.sales_rep_query",
	"Field Order": "field_sales.permissions.field_order_query",
	"Retailer": "field_sales.permissions.retailer_query",
	"Commission Entry": "field_sales.permissions.commission_entry_query",
	"Commission Statement": "field_sales.permissions.commission_statement_query",
}

has_permission = {
	"Visit": "field_sales.permissions.visit_has_permission",
	"Sales Rep": "field_sales.permissions.sales_rep_has_permission",
	"Field Order": "field_sales.permissions.field_order_has_permission",
	"Retailer": "field_sales.permissions.retailer_has_permission",
	"Commission Entry": "field_sales.permissions.commission_entry_has_permission",
	"Commission Statement": "field_sales.permissions.commission_statement_has_permission",
}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	# Purge Success Sync Logs older than 30 days; Failed logs are kept forever.
	"daily": [
		"field_sales.field_sales.doctype.sync_log.sync_log.clear_old_logs",
		# Keep the site's currency and time zone the same as the store's.
		"field_sales.sync.shop_settings.scheduled_sync",
	],
	# Razorpay payment links whose webhook never arrived (Link Sent, last 7 days, 50 a run).
	"hourly": [
		"field_sales.payments.razorpay.reconcile_open_links",
		# Confirmed store orders paid on Razorpay that the store does not show as paid yet.
		"field_sales.sync.orders.retry_store_payments",
	],
	# Ticks every 30 minutes; scheduled_sync itself honours Store Connection's sync frequency.
	"cron": {
		"*/30 * * * *": [
			"field_sales.sync.products.scheduled_sync",
			# Record the order id of drafts completed on the store after they synced.
			"field_sales.sync.orders.link_completed_drafts",
		],
		# Re-push Field Orders that failed (up to 5 attempts) or lost their job.
		"*/15 * * * *": [
			"field_sales.sync.orders.retry_failed_orders",
		],
		# Daily team summary to each manager, in the system timezone.
		"0 20 * * *": [
			"field_sales.daily_summary.send_daily_summary",
		],
		# Draft Commission Statements for last month, on the 1st.
		"0 2 1 * *": [
			"field_sales.commission.statements.generate_monthly_statements",
		],
	},
}

# Testing
# -------

# before_tests = "field_sales.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "field_sales.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "field_sales.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "field_sales.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["field_sales.utils.before_request"]
# Embedded /fieldsales page: frame-ancestors for the shop's admin (only set on that page).
after_request = ["field_sales.shopify.embedded.after_request"]

# Job Events
# ----------
# before_job = ["field_sales.utils.before_job"]
# after_job = ["field_sales.utils.after_job"]

# after_file_upload = ["field_sales.utils.after_file_upload"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# Shopify admin (embedded app): requests with an App Bridge session token sign in as
# Store Connection's embedded user. A no-op for every other request.
auth_hooks = ["field_sales.shopify.session_token.authenticate"]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# Require all whitelisted methods to have type annotations
require_type_annotated_api_methods = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []


# Fixtures
# ------------
# The Field Sales Order and Field Sales Invoice print formats (created on the site, versioned here).
fixtures = [
	{"dt": "Print Format", "filters": [["name", "in", ["Field Sales Order", "Field Sales Invoice"]]]},
]
