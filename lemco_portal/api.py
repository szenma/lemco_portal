import frappe
from frappe import _
from frappe.utils import now_datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_customer_for_user(user_email):
	"""Find the Customer linked to a portal user via their Contact record."""
	contact_name = frappe.db.get_value("Contact", {"email_id": user_email}, "name")
	if not contact_name:
		return None
	return frappe.db.get_value(
		"Dynamic Link",
		{"parent": contact_name, "parenttype": "Contact", "link_doctype": "Customer"},
		"link_name",
	)


def customer_has_portal_access(customer):
	if not customer:
		return False
	return frappe.db.get_value("Customer", customer, "portal_access") == "Yes"


# ---------------------------------------------------------------------------
# Signup: Create account -> Customer + Address + Contact + User (pending)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def create_customer_account(
	first_name,
	last_name,
	email,
	mobile_no,
	company_name,
	tax_id=None,
	website=None,
	address_title=None,
	address_line1=None,
	address_line2=None,
	pincode=None,
	city=None,
	country=None,
):
	required = {
		"Name": first_name,
		"Surname": last_name,
		"Email": email,
		"Mobile phone": mobile_no,
		"Company Name": company_name,
		"Address Line 1": address_line1,
		"ZIP code": pincode,
		"City/Town": city,
		"Country": country,
	}
	missing = [label for label, value in required.items() if not value]
	if missing:
		frappe.throw(_("Missing required field(s): {0}").format(", ".join(missing)))

	frappe.utils.validate_email_address(email, throw=True)

	if frappe.db.exists("Contact", {"email_id": email}):
		frappe.throw(_("An account with this email already exists. Try logging in instead."))

	# 1. Customer
	customer = frappe.new_doc("Customer")
	customer.customer_name = company_name
	customer.customer_type = "Company"
	if tax_id:
		customer.tax_id = tax_id
	if website:
		customer.website = website
	customer.portal_access = "No"
	customer.insert(ignore_permissions=True)

	# 2. Address — billing + shipping, linked to the new Customer
	address = frappe.new_doc("Address")
	address.address_title = address_title or company_name
	address.address_type = "Billing"
	address.address_line1 = address_line1
	address.address_line2 = address_line2
	address.pincode = pincode
	address.city = city
	address.country = country
	address.is_primary_address = 1
	address.is_shipping_address = 1
	address.append("links", {"link_doctype": "Customer", "link_name": customer.name})
	address.insert(ignore_permissions=True)

	# 3. Contact — primary contact, linked to the new Customer
	contact = frappe.new_doc("Contact")
	contact.first_name = first_name
	contact.last_name = last_name
	contact.append("email_ids", {"email_id": email, "is_primary": 1})
	contact.append("phone_nos", {"phone": mobile_no, "is_primary_mobile_no": 1})
	contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
	contact.insert(ignore_permissions=True)

	customer.customer_primary_contact = contact.name
	customer.customer_primary_address = address.name
	customer.save(ignore_permissions=True)

	# 4. Portal user account (Website User, no Desk access) - kept disabled
	# in spirit until portal_access flips to "Yes"; actual gate is enforced
	# in enforce_portal_access() below on every login.
	if not frappe.db.exists("User", email):
		user = frappe.new_doc("User")
		user.email = email
		user.first_name = first_name
		user.last_name = last_name
		user.user_type = "Website User"
		user.send_welcome_email = 1
		user.append("roles", {"role": "Customer"})
		user.insert(ignore_permissions=True)

	# 5. Confirmation email
	frappe.sendmail(
		recipients=[email],
		subject=_("Thanks for registering — Lemco Portal"),
		message=_(
			"Hi {0},<br><br>"
			"Thanks for creating an account on the Lemco Portal.<br>"
			"Your registration is now pending approval by our team. "
			"We'll email you as soon as your portal access is activated."
			"<br><br>Regards,<br>Lemco"
		).format(first_name),
		now=True,
	)

	return {"customer": customer.name}


# ---------------------------------------------------------------------------
# Login gate: only customers with Portal Access = Yes may use the portal
# ---------------------------------------------------------------------------

def enforce_portal_access(login_manager):
	"""Hook: frappe calls this via `on_session_creation` after every login."""
	user = login_manager.user
	if user in ("Administrator", "Guest"):
		return

	# Lemco employees (System Users / Desk users) are unaffected - this only
	# gates portal (Website User) customers.
	if frappe.db.get_value("User", user, "user_type") != "Website User":
		return

	customer = get_customer_for_user(user)
	if not customer:
		return  # not linked to a Customer at all - let other logic handle it

	if not customer_has_portal_access(customer):
		frappe.local.login_manager.logout()
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = "/login?pending=1"
		frappe.throw(_("Your account is pending approval."), frappe.PermissionError)


# ---------------------------------------------------------------------------
# Dashboard data (filtered to the logged-in customer)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_dashboard_data():
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	def count(doctype, filters):
		try:
			return frappe.db.count(doctype, filters)
		except Exception:
			return 0

	tickets_open = count("HD Ticket", {"customer": customer, "status": ["not in", ["Closed", "Resolved"]]})
	tickets_closed = count("HD Ticket", {"customer": customer, "status": ["in", ["Closed", "Resolved"]]})

	# NOTE: brief maps both "My Services" and "My Projects" to the same
	# /desk/project?status=Open URL - implemented as written; flag to client
	# if that duplication is intentional.
	projects_open = count("Project", {"customer": customer, "status": "Open"})
	projects_closed = count("Project", {"customer": customer, "status": ["!=", "Open"]})

	subs_open = count("Subscription", {"party": customer, "status": ["!=", "Cancelled"]})
	subs_closed = count("Subscription", {"party": customer, "status": "Cancelled"})

	return {
		"customer": customer,
		"tickets": {"open": tickets_open, "closed": tickets_closed},
		"projects": {"open": projects_open, "closed": projects_closed},
		"subscriptions": {"open": subs_open, "closed": subs_closed},
	}


@frappe.whitelist()
def get_recent_activity(limit=8):
	"""Best-effort activity feed for the logged-in customer: their own
	recent Comments/Communications via the Activity Log."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		return []

	return frappe.get_all(
		"Activity Log",
		filters={"user": frappe.session.user},
		fields=["subject", "creation", "operation"],
		order_by="creation desc",
		limit_page_length=int(limit),
	)


# ---------------------------------------------------------------------------
# Webinars
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def list_webinars():
	return frappe.get_all(
		"Webinar",
		filters={"status": "Scheduled"},
		fields=[
			"name", "title", "description", "webinar_date", "start_time",
			"duration_minutes", "language", "speaker", "teams_join_url",
		],
		order_by="webinar_date asc, start_time asc",
	)


@frappe.whitelist()
def register_for_webinar(webinar):
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	if frappe.db.exists("Webinar Registration", {"webinar": webinar, "customer": customer}):
		frappe.throw(_("You're already registered for this webinar."))

	webinar_doc = frappe.get_doc("Webinar", webinar)

	reg = frappe.new_doc("Webinar Registration")
	reg.webinar = webinar
	reg.customer = customer
	reg.contact_email = frappe.session.user
	reg.registered_on = now_datetime()

	if webinar_doc.get("teams_webinar_id"):
		# Real Teams Webinar, synced via Graph - register the attendee with
		# Teams itself so Teams sends its own registration/reminder emails.
		# Needs VirtualEventRegistration-Anon.ReadWrite.All (Application).
		from lemco_portal import graph

		contact = frappe.get_value(
			"Contact",
			{"email_id": frappe.session.user},
			["first_name", "last_name"],
			as_dict=True,
		) or {}

		result = graph.register_attendee(
			webinar_doc.teams_webinar_id,
			contact.get("first_name") or frappe.session.user,
			contact.get("last_name") or "",
			frappe.session.user,
		)
		reg.teams_registration_id = result.get("id")
		reg.join_url = result.get("joinWebUrl") or result.get("joinWebURL")
		reg.insert(ignore_permissions=True)
		return {"join_url": reg.join_url, "via": "teams"}

	# Manually-entered webinar (no Teams sync) - fall back to our own email
	# with the join link the staff member pasted in.
	reg.join_url = webinar_doc.get("teams_join_url")
	reg.insert(ignore_permissions=True)

	frappe.sendmail(
		recipients=[frappe.session.user],
		subject=_("You're registered: {0}").format(webinar_doc.title),
		message=_(
			"Hi,<br><br>You're registered for <b>{0}</b> on {1}.<br>"
			'Join link: <a href="{2}">{2}</a><br><br>See you there!'
		).format(webinar_doc.title, webinar_doc.webinar_date, webinar_doc.teams_join_url or ""),
		now=True,
	)

	return {"join_url": reg.join_url, "via": "email"}
