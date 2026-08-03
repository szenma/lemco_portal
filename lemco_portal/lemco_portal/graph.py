import frappe
import requests
from frappe.utils import get_datetime

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_CACHE_KEY = "lemco_portal_graph_token"


# ---------------------------------------------------------------------------
# Auth (client credentials flow)
# ---------------------------------------------------------------------------

def _get_settings():
	settings = frappe.get_single("Lemco Portal Settings")
	if not (settings.tenant_id and settings.client_id and settings.get_password("client_secret", raise_exception=False)):
		frappe.throw(
			"Set Tenant ID, Client ID and Client Secret in "
			"Lemco Portal Settings (/app/lemco-portal-settings) first."
		)
	return settings


def get_access_token():
	cached = frappe.cache().get_value(TOKEN_CACHE_KEY)
	if cached:
		return cached

	settings = _get_settings()
	url = f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token"
	data = {
		"client_id": settings.client_id,
		"client_secret": settings.get_password("client_secret"),
		"scope": "https://graph.microsoft.com/.default",
		"grant_type": "client_credentials",
	}
	resp = requests.post(url, data=data, timeout=15)
	resp.raise_for_status()
	token_data = resp.json()

	token = token_data["access_token"]
	expires_in = int(token_data.get("expires_in", 3600))
	# cache a little short of actual expiry so we never use a stale token
	frappe.cache().set_value(TOKEN_CACHE_KEY, token, expires_in_sec=max(60, expires_in - 120))
	return token


def _headers():
	return {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Webinars — List webinars needs VirtualEvent.Read.All (Application)
# ---------------------------------------------------------------------------

def list_teams_webinars():
	"""GET /solutions/virtualEvents/webinars - tenant-wide.
	Only returns webinars whose organizer has a Teams Application Access
	Policy assigned to this app (see README)."""
	url = f"{GRAPH_BASE}/solutions/virtualEvents/webinars"
	resp = requests.get(url, headers=_headers(), timeout=20)
	resp.raise_for_status()
	return resp.json().get("value", [])


@frappe.whitelist()
def sync_webinars_now():
	"""Manually trigger a sync - call from Desk console or a client script
	button on the Webinar list while testing, instead of waiting an hour."""
	frappe.only_for("System Manager")
	sync_webinars()
	return {"ok": True}


def sync_webinars():
	"""Scheduled job: pull Teams webinars into the local Webinar doctype,
	so /webinars keeps working even during a brief Graph outage, and so
	the rest of the portal (dashboard widget etc.) doesn't need to call
	Graph directly on every page load."""
	try:
		teams_webinars = list_teams_webinars()
	except Exception:
		frappe.log_error(title="Lemco Portal: Teams webinar sync failed")
		return

	for w in teams_webinars:
		try:
			upsert_webinar(w)
		except Exception:
			frappe.log_error(title=f"Lemco Portal: failed to sync webinar {w.get('id')}")


def upsert_webinar(w):
	teams_id = w.get("id")
	if not teams_id:
		return

	existing = frappe.db.get_value("Webinar", {"teams_webinar_id": teams_id}, "name")
	doc = frappe.get_doc("Webinar", existing) if existing else frappe.new_doc("Webinar")

	doc.teams_webinar_id = teams_id
	doc.title = w.get("displayName") or doc.get("title") or teams_id
	doc.description = (w.get("description") or {}).get("content") or ""

	start_raw = (w.get("startDateTime") or {}).get("dateTime")
	end_raw = (w.get("endDateTime") or {}).get("dateTime")
	if start_raw:
		start_dt = get_datetime(start_raw)
		doc.webinar_date = start_dt.date()
		doc.start_time = start_dt.time()
		if end_raw:
			try:
				doc.duration_minutes = int((get_datetime(end_raw) - start_dt).total_seconds() // 60)
			except Exception:
				pass

	status = w.get("status")
	if status == "published":
		doc.status = "Scheduled"
	elif status == "canceled":
		doc.status = "Cancelled"
	elif not doc.get("status"):
		doc.status = "Scheduled"

	if existing:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Registration — needs VirtualEventRegistration-Anon.ReadWrite.All (Application)
# Teams sends the registration/confirmation email itself once this succeeds.
# ---------------------------------------------------------------------------

def register_attendee(teams_webinar_id, first_name, last_name, email):
	url = f"{GRAPH_BASE}/solutions/virtualEvents/webinars/{teams_webinar_id}/registrations"
	body = {
		"@odata.type": "#microsoft.graph.virtualEventRegistration",
		"firstName": first_name,
		"lastName": last_name,
		"email": email,
		"registrationStatus": "registered",
	}
	resp = requests.post(url, headers=_headers(), json=body, timeout=20)
	resp.raise_for_status()
	if not resp.content:
		return {}
	return resp.json()
