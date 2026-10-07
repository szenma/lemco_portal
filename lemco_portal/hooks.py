app_name = "lemco_portal"
app_title = "Lemco Portal"
app_publisher = "Lemco IKE"
app_description = "Lemco customer portal (Website/Portal pages on top of Helpdesk, LMS, and CRM)"
app_email = "support@lemco.example"
app_license = "mit"

# Includes in <head>
# ------------------

# Loaded automatically by every www/*.html page via <link>/<script> tags in
# each template, so nothing needs to be declared here for those.

# Home Pages
# ----------

# Where a "Website User" (portal customer) lands after logging in.
role_home_page = {
	"Customer": "dashboard",
}

# Website redirects
# ------------------
# ERPNext ships the Project doctype as a built-in "website generator" with
# its own native, unstyled /projects/list, /projects/new, and /projects/<name>
# pages - completely separate from our own custom /projects page in this app,
# and not something we can safely turn off from the DocType/Website Settings
# UI without risking other ERPNext functionality. Redirect anything under
# /projects/ (i.e. anything with a path segment after "projects") back to our
# own page instead, so customers can never land on the native ERPNext one.
# Bare "/projects" itself is untouched - that's served by our app.
website_redirects = [
	{"source": r"^/projects/.*", "target": "/projects"},
]

# Pretty per-record RMA URLs (spec: /repairs/RMA-2026-00142). This rewrites
# the URL internally to a physical page at www/repairs_detail without an
# HTTP redirect - the browser keeps seeing /repairs/<name>. The physical
# page reads the captured rma_name via frappe.form_dict.
website_route_rules = [
	{"from_route": "/repairs/<rma_name>", "to_route": "repairs_detail"},
	{"from_route": "/rma/access/<unit_name>", "to_route": "rma_access"},
]

# Read-only permission for RMA, scoped to the caller's own Customer.
# Portal users still have NO general Desk-level permission on RMA - all
# reads/writes go through api.py's whitelisted, field-whitelisted methods.
# This exists for one narrow reason: Frappe's own private-file download
# checks permission on the attached document directly, so without this a
# customer couldn't open a photo they just uploaded to their own RMA.
has_permission = {
	"RMA": "lemco_portal.api.rma_has_permission",
}

# Desk look for the RMA form / RMA Item row editor (layout itself is applied by
# api.setup_backend_layout; these two files only add styling and status pills).
app_include_css = "/assets/lemco_portal/css/lemco_desk.css"
app_include_js = "/assets/lemco_portal/js/lemco_desk.js"

# Adds the status-action buttons (Approve/Reject/Receive/Ship/etc) to the
# RMA and RMA Unit Desk forms - the underlying whitelisted methods in
# api.py are usable without this, but staff would otherwise have no way
# to call them except a raw frappe.call() from the browser console.
doctype_js = {
	"RMA": "public/js/rma.js",
	"RMA Unit": "public/js/rma_unit.js",
}

doctype_list_js = {
	"RMA": "public/js/rma_list.js",
}

# Installation
# ------------

after_install = "lemco_portal.install.after_install"

# Runs once, unconditionally, at the very end of every `bench migrate` -
# after all patches (pre- and post-model-sync) and all DocType schema
# sync have finished. Used for the two RMA setup steps that reference
# this app's own DocTypes (RMA, RMA Unit, RMA Settings) - see the note in
# patches.txt for why this hook is used instead of a patch for these two.
# Both functions are safe to re-run on every migrate (update-in-place,
# not duplicate).
after_migrate = [
	"lemco_portal.install.create_rma_print_formats",
	"lemco_portal.install.create_repair_service_workspace",
]

# Login gate
# ----------
# Fires after every successful login. Logs the user back out if they're a
# portal customer whose Customer.portal_access field isn't "Yes" yet.
on_session_creation = "lemco_portal.api.enforce_portal_access"

# Doc events
# ----------
# Emails the customer's primary contact the moment a Lemco employee
# approves their Portal Access (No -> Yes) on the Customer record.
# Also auto-fills Customer on tickets portal users create - Helpdesk never
# sets it itself, and everything else in this app (dashboard counts, etc.)
# assumes it's populated.
doc_events = {
	"Customer": {
		"on_update": "lemco_portal.api.notify_portal_access_approved",
	},
	"HD Ticket": {
		"before_insert": "lemco_portal.api.set_ticket_customer",
	},
	"RMA Unit": {
		"on_update": "lemco_portal.api.notify_rma_unit_technician_change",
	},
}

# Jinja methods available inside Print Format templates (spec section 115 -
# the four RMA print formats need a QR code image and some data that isn't
# directly on the document being printed, e.g. RMA Unit pulling its parent
# RMA's customer info and its originating RMA Item's problem description).
jinja = {
	"methods": [
		"lemco_portal.api.get_qr_code_data_uri",
		"lemco_portal.api.get_rma_print_context",
		"lemco_portal.api.get_rma_item_for_unit",
	],
}

# Scheduled tasks
# ----------------
# Pulls Teams Webinars into the local Webinar doctype every hour.
# Requires VirtualEvent.Read.All (Application) + admin consent, and each
# organizer to have a Teams Application Access Policy for this app.
scheduler_events = {
	"hourly": [
		"lemco_portal.graph.sync_webinars",
	],
}

# Fixtures
# --------
# Uncomment if you want the Page doctype record exported/synced via fixtures
# instead of relying on the .json file under the page folder.
# fixtures = ["Page"]

# Dependencies
# ------------
# Tells bench/Frappe Cloud to make sure these apps are installed on the
# site before this one. Remove any you don't actually use - the page
# degrades gracefully (shows 0) if a doctype from a missing app isn't found,
# but installation will fail without this if the apps aren't present.
required_apps = ["helpdesk", "lms", "crm"]
