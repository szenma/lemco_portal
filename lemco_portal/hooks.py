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

# Installation
# ------------

after_install = "lemco_portal.install.after_install"

# Login gate
# ----------
# Fires after every successful login. Logs the user back out if they're a
# portal customer whose Customer.portal_access field isn't "Yes" yet.
on_session_creation = "lemco_portal.api.enforce_portal_access"

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
