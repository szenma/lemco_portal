import frappe
from frappe import _
from datetime import timedelta
from frappe.utils import now_datetime, today , get_url , get_datetime
#from frappe.utils.password import update_password
import hashlib
import secrets

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot_session():
	"""Capture the parts of the request/session state that frappe.set_user()
	destroys. set_user() does: session.sid = username, session.data = {},
	form_dict = {}. session.data is the persisted session payload (logged-in
	user, csrf_token, ...) and Frappe writes it back to the Sessions table and
	cache at the end of every request - so if it is left wiped, the NEXT
	request resumes a session whose user is None ("User None not found") and
	then falls back to Guest."""
	return (frappe.local.session.sid, frappe.local.session.data, frappe.local.form_dict)


def _restore_session(user, snapshot):
	sid, session_data, form_dict = snapshot
	frappe.set_user(user)
	frappe.local.session.sid = sid
	frappe.local.session.data = session_data
	frappe.local.form_dict = form_dict


def _lemco_email_html(body_html, heading=None):
	"""The ONE email layout used by every automated Lemco Portal email:
	logo top-left, white content card with a heading, standard sign-off,
	and a small automated-message footer. Table-based with inline styles so
	it renders the same in Outlook, Gmail and Apple Mail."""
	logo = get_url("/assets/lemco_portal/images/lemco-logo.png")
	heading_html = ""
	if heading:
		heading_html = (
			'<h2 style="margin:0 0 18px;font-size:20px;line-height:1.3;color:#111827;'
			'font-family:Arial,Helvetica,sans-serif;">' + frappe.utils.escape_html(heading) + "</h2>"
		)
	return (
		'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
		'style="background:#f4f5f7;padding:24px 12px;"><tr><td align="center">'
		'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
		'style="width:100%;max-width:600px;font-family:Arial,Helvetica,sans-serif;">'
		# header: logo, upper-left
		'<tr><td align="left" style="padding:0 0 16px;">'
		'<img src="' + logo + '" alt="Lemco" width="150" '
		'style="display:block;border:0;outline:none;max-width:150px;height:auto;">'
		"</td></tr>"
		# content card
		'<tr><td style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:32px;">'
		+ heading_html +
		'<div style="font-size:14px;line-height:1.6;color:#374151;">' + body_html + "</div>"
		'<p style="margin:28px 0 0;font-size:14px;line-height:1.6;color:#374151;">'
		"Kind regards,<br>The Lemco Team</p>"
		"</td></tr>"
		# footer
		'<tr><td align="left" style="padding:16px 4px 0;font-size:12px;line-height:1.5;color:#9ca3af;">'
		"This is an automated message from the Lemco Customer Portal."
		"</td></tr>"
		"</table></td></tr></table>"
	)


def _lemco_sendmail(recipients, subject, message, now=False):
	"""Use this instead of frappe.sendmail for every automated email, so all
	of them share _lemco_email_html. The subject doubles as the heading."""
	frappe.sendmail(
		recipients=recipients,
		subject=subject,
		message=_lemco_email_html(message, heading=subject),
		now=now,
	)


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


def get_hd_customer_for_user(user_email):
	"""Find the HD Customer linked to a portal user. HD Ticket.customer
	links to HD Customer, not the core Customer above - a separate,
	Helpdesk-only doctype used for ticket routing/SLAs, decoupled from
	ERPNext's Selling module. HD Customer holds the relationship itself
	(a primary_contact field + a child table of member contacts) rather
	than Contact holding a Dynamic Link back to it the way core Customer
	does, so instead of depending on that child table's internal field
	names, we resolve it the same reliable way every other org-scoping
	lookup in this file works: a User Permission set at registration/
	create_org_user time."""
	return frappe.db.get_value(
		"User Permission",
		{"user": user_email, "allow": "HD Customer"},
		"for_value",
	)


def customer_has_portal_access(customer):
	if not customer:
		return False
	return frappe.db.get_value("Customer", customer, "portal_access") == "Yes"


def _create_hd_customer(company_name, contact_name, email, mobile_no=None):
	"""Creates the HD Customer that HD Ticket.customer actually links to -
	separate from the core Customer created alongside it, which Project/
	Subscription link to instead. Call this with Administrator privileges
	already active (same pattern as the rest of registration/create_org_user)."""
	hd_customer = frappe.new_doc("HD Customer")
	hd_customer.customer_name = company_name
	hd_customer.customer_type = "Company"
	hd_customer.primary_contact = contact_name
	hd_customer.email_id = email
	if mobile_no:
		hd_customer.mobile_no = mobile_no
	if email and "@" in email:
		hd_customer.domain = email.split("@")[-1]
	_add_hd_customer_member(hd_customer, contact_name, is_manager=True)
	hd_customer.insert(ignore_permissions=True)
	return hd_customer


def _add_hd_customer_member(hd_customer_doc, contact_name, is_manager=False):
	"""Best-effort append into HD Customer's "contacts" child table. Reads
	the child doctype and its Link-to-Contact fieldname from the DocType
	meta at runtime instead of hardcoding names we haven't verified, and
	never lets a mismatch there break account/user creation - the User
	Permission this app actually depends on for scoping is set separately
	by the caller regardless of whether this succeeds.

	Also best-effort sets whatever field marks manager status on that
	same row. Per Helpdesk's own docs (docs.frappe.io/helpdesk/
	customers-contacts), "regular contacts only see their own tickets;
	managers see all" is enforced by Helpdesk itself based on this
	per-Customer relationship - not solely by the HD Customer Manager
	Frappe role we also assign separately. Skipping this silently would
	mean a Manager's own visibility doesn't actually widen even though
	the role looks right."""
	try:
		child_field = frappe.get_meta("HD Customer").get_field("contacts")
		if not child_field:
			return
		child_meta = frappe.get_meta(child_field.options)

		link_fieldname = next(
			(df.fieldname for df in child_meta.fields if df.fieldtype == "Link" and df.options == "Contact"),
			None,
		)
		if not link_fieldname:
			return

		row = {link_fieldname: contact_name}

		# Best-effort: find a field that looks like it marks manager
		# status - either a Select whose options include "HD Customer
		# Manager"/"Manager", or a Check/boolean field with "manager" in
		# its fieldname.
		manager_field = next(
			(
				df for df in child_meta.fields
				if df.fieldtype == "Select"
				and df.options
				and any("manager" in opt.lower() for opt in df.options.split("\n"))
			),
			None,
		)
		if manager_field:
			options = [o for o in manager_field.options.split("\n") if o]
			manager_opt = next((o for o in options if "manager" in o.lower()), None)
			non_manager_opt = next((o for o in options if o != manager_opt), None)
			row[manager_field.fieldname] = (manager_opt if is_manager else non_manager_opt) or ""
		else:
			bool_field = next(
				(
					df for df in child_meta.fields
					if df.fieldtype == "Check" and "manager" in df.fieldname.lower()
				),
				None,
			)
			if bool_field:
				row[bool_field.fieldname] = 1 if is_manager else 0

		hd_customer_doc.append("contacts", row)
	except Exception:
		frappe.log_error(
			title="Lemco Portal: could not append HD Customer member contact",
			message=f"hd_customer contact_name={contact_name} is_manager={is_manager}",
		)


def _grant_hd_customer_permission(user_email, hd_customer_name):
	if not frappe.db.exists("User Permission", {"user": user_email, "allow": "HD Customer", "for_value": hd_customer_name}):
		frappe.get_doc({
			"doctype": "User Permission",
			"user": user_email,
			"allow": "HD Customer",
			"for_value": hd_customer_name,
			"apply_to_all_doctypes": 1,
		}).insert(ignore_permissions=True)


def notify_portal_access_approved(doc, method=None):
	"""doc_event hook on Customer.on_update - fires the moment a Lemco
	employee flips Portal Access to Yes. Generates a random password for
	the user and emails it directly using the client's approved Welcome
	Email template, rather than a set-your-own-password link.

	NOTE ON SECURITY: emailing a password directly (rather than a
	set-password link) is less secure in general - if the email is
	intercepted, the password is exposed in plaintext with no expiry.
	This is a deliberate choice per the client's explicit instruction,
	not an oversight. The email itself does tell the recipient to change
	their password after first login, per the client's template."""
	if not doc.has_value_changed("portal_access") or doc.portal_access != "Yes":
		return

	if not doc.customer_primary_contact:
		return

	contact_email = frappe.db.get_value("Contact", doc.customer_primary_contact, "email_id")
	if not contact_email:
		return

	if not frappe.db.exists("User", contact_email):
		return

	# Generate a random password and set it directly on the User - Frappe's
	# User controller hashes whatever is assigned to new_password on save.
	random_password = _generate_random_password()
	user = frappe.get_doc("User", contact_email)
	user.new_password = random_password
	user.save(ignore_permissions=True)

	message = _(
		"Dear Customer,<br><br>"
		"Welcome to the Lemco Customer Portal.<br><br>"
		"Your account has been successfully created, giving you access to a "
		"centralized environment where you can manage your interaction with "
		"Lemco, access useful information and services, and stay connected "
		"with our team.<br><br>"
		"<b>Your Login Details</b><br>"
		"Username: {0}<br>"
		"Password: {1}<br><br>"
		"You can access the Lemco Customer Portal at:<br>"
		'<a href="{2}">{2}</a><br><br>'
		"For security reasons, we recommend changing your password after "
		"your first login and keeping your credentials confidential.<br><br>"
		"Through the Lemco Customer Portal, you can access available "
		"services such as:<br>"
		"- Support and service requests<br>"
		"- Product and installation information<br>"
		"- Your registered equipment and related services<br>"
		"- Technical resources and documentation<br>"
		"- Available webinars and training sessions<br>"
		"- Updates and announcements from Lemco<br><br>"
		"We are continuously developing the portal to provide you with an "
		"easier, faster, and more efficient way to work with Lemco.<br><br>"
		"If you experience any difficulty accessing your account or require "
		"assistance, please contact the Lemco team.<br><br>"
		"Thank you for choosing Lemco. We are pleased to have you with us."
	).format(contact_email, random_password, frappe.utils.get_url("/login"))

	_lemco_sendmail(
		recipients=[contact_email],
		subject=_("Welcome to the Lemco Customer Portal"),
		message=message,
		now=True,
	)


def _generate_random_password(length=12):
	import secrets
	import string

	alphabet = string.ascii_letters + string.digits
	# Guarantee at least one digit and one uppercase letter for basic complexity
	pw = [secrets.choice(string.ascii_uppercase), secrets.choice(string.digits)]
	pw += [secrets.choice(alphabet) for _ in range(length - 2)]
	secrets.SystemRandom().shuffle(pw)
	return "".join(pw)


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

	# HD Customer is named directly from the company name (no auto-numbering,
	# unlike core Customer below) - a repeat registration attempt using the
	# same company name would otherwise fail later with a raw, unfriendly
	# "HD Customer X already exists" error instead of a clear one here.
	if frappe.db.exists("HD Customer", company_name):
		frappe.throw(_("An account for the company \"{0}\" already exists. Please contact us if you need access.").format(company_name))

	# Country is a Link field to the Country doctype and needs an exact
	# case match ("India", not "india") - resolve it case-insensitively
	# here instead of relying on the customer typing it exactly right.
	resolved_country = frappe.db.get_value("Country", {"name": ["like", country]}, "name")
	if not resolved_country:
		frappe.throw(_("'{0}' isn't a recognised country name.").format(country))
	country = resolved_country

	# Guest has no doctype permissions at all, and ignore_permissions=True on
	# our own .insert() calls only covers the top-level insert - it doesn't
	# propagate into permission-checked lookups that Contact/Address/Customer
	# controllers sometimes do internally during validate()/autoname(). The
	# reliable fix is to briefly elevate to Administrator for just the
	# record-creation step, then always restore the original session user.
	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
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
		# Explicitly set - ERPNext's own Address.validate() reads this via dot
		# notation with no default, which throws AttributeError instead of a
		# normal validation error if a site's DocType meta cache is stale and
		# doesn't have this standard field loaded. Setting it here avoids that
		# crash outright regardless of the underlying cache state.
		address.is_your_company_address = 0
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

		# 3b. HD Customer — separate from the core Customer above. HD
		# Ticket.customer links to this doctype, not core Customer, so
		# without this every ticket a portal user creates fails to link
		# up correctly (blank customer field, "not linked to contact"
		# errors, wrong dashboard ticket counts).
		#hd_customer = _create_hd_customer(company_name, contact.name, email, mobile_no)

		# 4. Portal user account (Website User, no Desk access) - kept
		# disabled in spirit until portal_access flips to "Yes"; actual gate
		# is enforced in enforce_portal_access() below on every login.
		if not frappe.db.exists("User", email):
			user = frappe.new_doc("User")
			user.email = email
			user.first_name = first_name
			user.last_name = last_name
			user.user_type = "Website User"
			user.send_welcome_email = 0
			# The user created here is always the *first* user of a brand-new
			# Customer (registration never adds a user to an existing
			# company - that's create_org_user() below), so they always get
			# HD Customer Manager. Any users this person later adds via the
			# Users page get plain HD Customer instead (see create_org_user).
			for role in ("Customer", "HD Customer Manager", "LMS Student"):
				user.append("roles", {"role": role})
			user.insert(ignore_permissions=True)

		# Declares this as a portal user scoped to their own Customer record.
		# Any doctype whose permission rules respect User Permissions (Link
		# field to Customer, "Apply User Permission" checked) will now
		# automatically restrict this user to only their own records.
		if not frappe.db.exists("User Permission", {"user": email, "allow": "Customer", "for_value": customer.name}):
			frappe.get_doc({
				"doctype": "User Permission",
				"user": email,
				"allow": "Customer",
				"for_value": customer.name,
				"apply_to_all_doctypes": 1,
			}).insert(ignore_permissions=True)

		#_grant_hd_customer_permission(email, hd_customer.name)
	except Exception as e:
		# Anything failing partway through this sequence - Customer,
		# Address, Contact, HD Customer, User, User Permission - would
		# otherwise leave whichever of those already succeeded sitting in
		# the database as orphaned records, which then block every future
		# registration attempt with confusing "already exists" errors
		# (this is exactly what happened: an HD Customer named after the
		# company got created, a later step failed, and every retry using
		# that same company name collided with the leftover). Roll back
		# everything from this attempt either way.
		frappe.db.rollback()
		if isinstance(e, frappe.DuplicateEntryError):
			# The early exists()-check above can still race with a second,
			# near-simultaneous submission (e.g. a double-click on Submit):
			# neither request sees the other's uncommitted insert, so both
			# pass that check, and only the database's own primary-key
			# constraint catches the second one - surfacing here as a raw
			# DuplicateEntryError instead of the friendly message the
			# pre-check normally gives. Same friendly wording either way.
			frappe.throw(_(
				"An account for the company \"{0}\" already exists. "
				"Please contact us if you need access."
			).format(company_name))
		raise
	finally:
		_restore_session(original_user, original_sid)

	# 5. Confirmation email
	_lemco_sendmail(
		recipients=[email],
		subject=_("Welcome to the Lemco Portal – Registration Received"),
		message=_(
			"Hi {0},<br><br>"
			"Thanks for creating an account on the Lemco Portal.<br><br>"
			"Your registration has been received successfully and is currently pending approval by our team. "
			"Once your account has been approved and your portal access activated, you will receive a confirmation email."
		).format(first_name),
		now=True,
	)

	return {"customer": customer.name}


# ---------------------------------------------------------------------------
# Login gate: only customers with Portal Access = Yes may use the portal
# ---------------------------------------------------------------------------

def enforce_portal_access(login_manager):
	"""Hook: frappe calls this via `on_session_creation` after every login -
	including logins that happen via Frappe's own built-in /update-password
	page (e.g. right after a password-reset email link), which does NOT
	reliably honor the role_home_page hook in practice. So instead of
	relying on that, we force the redirect explicitly here every time."""
	user = login_manager.user
	if user in ("Administrator", "Guest"):
		return

	# Lemco employees (System Users / Desk users) are unaffected - this only
	# gates/redirects portal (Website User) customers.
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

	# Approved portal customer, logging in from anywhere (our /login page,
	# the default /login, or straight after /update-password) - always land
	# on the portal dashboard, never Desk.
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = "/dashboard"


# ---------------------------------------------------------------------------
# Org user management: portal-page equivalent of "Manage Users" - the
# HD Customer Manager can list and add teammates from /users. No Desk
# access or Frappe role permissions are involved anywhere in this section;
# access is enforced entirely in Python via get_customer_for_user(), the
# same pattern the rest of this file already uses for projects/tickets/etc.
# ---------------------------------------------------------------------------

def _assert_org_manager():
	"""Raise unless the logged-in user is the HD Customer Manager for their
	own organization - i.e. the primary contact created at registration.
	Returns that person's Customer name."""
	if frappe.session.user in ("Administrator", "Guest"):
		frappe.throw(_("Not permitted."), frappe.PermissionError)

	if "HD Customer Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw(_("Only your organization's primary contact can add new users."), frappe.PermissionError)

	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	return customer


@frappe.whitelist()
def get_my_org_role():
	"""Tells the /users page whether to show the 'Add user' form - only the
	HD Customer Manager gets it."""
	customer = get_customer_for_user(frappe.session.user)
	return {
		"email": frappe.session.user,
		"customer": customer,
		"is_manager": "HD Customer Manager" in frappe.get_roles(frappe.session.user),
	}


@frappe.whitelist()
def list_org_users():
	"""Users of the logged-in customer's own organization, for the /users
	portal page: the organization's manager (the Customer's primary contact)
	plus every user added under that Customer."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	# Members are gathered from every link a user can have to the Customer:
	# a Customer User Permission (set by registration/create_org_user), or a
	# Contact linked to the Customer (all a user created by hand in Desk has).
	emails = set(frappe.get_all(
		"User Permission",
		filters={"allow": "Customer", "for_value": customer},
		pluck="user",
	))

	contact_names = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
	)
	if contact_names:
		emails.update(frappe.get_all("Contact", filters={"name": ["in", contact_names]}, pluck="email_id"))
		emails.update(frappe.get_all("Contact Email", filters={"parent": ["in", contact_names]}, pluck="email_id"))

	# The customer's Primary Contact is the organization's manager - the same
	# contact the Welcome/Invite email is sent to - so always list them, however
	# their User was created. And the logged-in user is by definition a member
	# of the org they are looking at, so they must always see themselves.
	primary_contact = frappe.db.get_value("Customer", customer, "customer_primary_contact")
	if primary_contact:
		emails.add(frappe.db.get_value("Contact", primary_contact, "email_id"))
		emails.update(frappe.get_all("Contact Email", filters={"parent": primary_contact}, pluck="email_id"))
	emails.add(frappe.session.user)

	emails = [e for e in emails if e and e not in ("Administrator", "Guest")]
	if not emails:
		return []

	users = frappe.get_all(
		"User",
		filters={"name": ["in", emails]},
		fields=["name", "full_name", "enabled"],
		order_by="creation asc",
	)
	for u in users:
		u["is_manager"] = "HD Customer Manager" in frappe.get_roles(u["name"])
	return users


@frappe.whitelist()
def create_org_user(first_name, last_name=None, email=None, mobile_no=None):
	"""Called from the 'Add user' form on the /users portal page. Only an
	HD Customer Manager may call this, and the new user is always added
	under that manager's own Customer with the plain HD Customer role
	(never HD Customer Manager - see create_customer_account for why only
	the first/registering user gets that)."""
	customer = _assert_org_manager()

	if not first_name or not email:
		frappe.throw(_("First name and email are required."))

	frappe.utils.validate_email_address(email, throw=True)

	if frappe.db.exists("User", email) or frappe.db.exists("Contact", {"email_id": email}):
		frappe.throw(_("A user with this email already exists."))

	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		contact = frappe.new_doc("Contact")
		contact.first_name = first_name
		contact.last_name = last_name
		contact.append("email_ids", {"email_id": email, "is_primary": 1})
		if mobile_no:
			contact.append("phone_nos", {"phone": mobile_no, "is_primary_mobile_no": 1})
		contact.append("links", {"link_doctype": "Customer", "link_name": customer})
		contact.insert(ignore_permissions=True)

		hd_customer = get_hd_customer_for_user(original_user)
		if hd_customer:
			hd_customer_doc = frappe.get_doc("HD Customer", hd_customer)
			_add_hd_customer_member(hd_customer_doc, contact.name)
			hd_customer_doc.save(ignore_permissions=True)

		user = frappe.new_doc("User")
		user.email = email
		user.first_name = first_name
		user.last_name = last_name
		user.user_type = "Website User"
		user.send_welcome_email = 0
		for role in ("Customer", "HD Customer", "LMS Student"):
			user.append("roles", {"role": role})
		user.insert(ignore_permissions=True)

		if not frappe.db.exists("User Permission", {"user": email, "allow": "Customer", "for_value": customer}):
			frappe.get_doc({
				"doctype": "User Permission",
				"user": email,
				"allow": "Customer",
				"for_value": customer,
				"apply_to_all_doctypes": 1,
			}).insert(ignore_permissions=True)

		if hd_customer:
			_grant_hd_customer_permission(email, hd_customer)

		# New teammate is added by someone already inside an approved
		# organization, so - unlike the registration flow - there's no
		# pending-approval step: email their login straight away.
		random_password = _generate_random_password()
		added_user = frappe.get_doc("User", email)
		added_user.new_password = random_password
		added_user.save(ignore_permissions=True)
	finally:
		_restore_session(original_user, original_sid)

	_lemco_sendmail(
		recipients=[email],
		subject=_("You've been added to the Lemco Customer Portal"),
		message=_(
			"Hi {0},<br><br>"
			"{1} has added you as a user to the Lemco Customer Portal.<br><br>"
			"<b>You can now sign in using the credentials below:</b><br>"
			"Username: {2}<br>"
			"Temporary Password: {3}<br><br>"
			"Access Customer Portal<br>"
			'<a href="{4}">{4}</a><br><br>'
			"For security reasons, please change your temporary password immediately after your first sign-in.<br>"
			"If you were not expecting this invitation, please contact the Lemco support team."
		).format(
			first_name,
			frappe.utils.get_fullname(original_user),
			email,
			random_password,
			frappe.utils.get_url("/login"),
		),
		now=True,
	)

	return {"user": email}


# ---------------------------------------------------------------------------
# Dashboard data (filtered to the logged-in customer)
# ---------------------------------------------------------------------------

def _is_org_manager(user_email):
	"""Whether this portal user is the org's admin - reusing the same
	HD Customer Manager role Helpdesk already uses for ticket visibility,
	as the single "who's the manager" signal across the whole portal.
	Project/Subscription visibility below is enforced independently of
	Helpdesk's own HD Customer mechanism (they link to core Customer, not
	HD Customer) - this only reuses the role as a convenient shared flag,
	not any of HD Customer's data."""
	return "HD Customer Manager" in frappe.get_roles(user_email)


@frappe.whitelist()
def list_my_projects(status=None):
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	filters = {"customer": customer}
	if not _is_org_manager(frappe.session.user):
		# Regular members only see projects they themselves created;
		# the org Manager sees everything under the Customer.
		filters["owner"] = frappe.session.user
	if status:
		filters["status"] = status

	try:
		return frappe.get_all(
			"Project",
			filters=filters,
			fields=["name", "project_name", "status", "expected_start_date", "expected_end_date", "percent_complete"],
			order_by="modified desc",
		)
	except Exception:
		return []


@frappe.whitelist()
def create_project(project_name, expected_start_date=None, expected_end_date=None, notes=None):
	"""Self-service project creation from the /projects portal page. Any
	logged-in user belonging to a customer (not just the org Manager) can
	create one - projects aren't an org-admin action the way adding a
	teammate is. The creating user is recorded as the project's owner so
	list_my_projects can tell "my own" apart from "the whole org's"."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	if not project_name:
		frappe.throw(_("Project name is required."))

	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		project = frappe.new_doc("Project")
		project.project_name = project_name
		project.customer = customer
		project.status = "Open"
		project.owner = original_user
		if expected_start_date:
			project.expected_start_date = expected_start_date
		if expected_end_date:
			project.expected_end_date = expected_end_date
		if notes:
			project.notes = notes
		project.insert(ignore_permissions=True)
	finally:
		_restore_session(original_user, original_sid)

	return {"project": project.name}


@frappe.whitelist()
def list_my_subscriptions(status=None):
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	filters = {"party_type": "Customer", "party": customer}
	if not _is_org_manager(frappe.session.user):
		filters["owner"] = frappe.session.user
	if status:
		filters["status"] = status

	try:
		return frappe.get_all(
			"Subscription",
			filters=filters,
			fields=["name", "status", "current_invoice_start", "current_invoice_end"],
			order_by="modified desc",
		)
	except Exception:
		return []


@frappe.whitelist()
def list_subscription_plans():
	"""Active Subscription Plans customers can pick from when creating a new
	subscription. ASSUMPTION: this app uses Frappe's core "Subscription
	Plan" doctype to define what's purchasable - if Lemco's actual plans
	live in a different doctype, swap the source here."""
	return frappe.get_all(
		"Subscription Plan",
		fields=["name", "plan_name", "cost", "currency", "billing_interval", "billing_interval_count"],
		order_by="plan_name asc",
	)


@frappe.whitelist()
def create_subscription(plan, start_date=None):
	"""Self-service subscription creation from the /subscriptions portal
	page. Creates a Subscription for the logged-in user's Customer against
	the chosen Subscription Plan; Frappe's own Subscription controller
	takes over status/invoicing from there (Trialling/Active/etc.) exactly
	as it would for one created from Desk. The creating user is recorded
	as the subscription's owner so list_my_subscriptions can tell "my
	own" apart from "the whole org's"."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	if not plan:
		frappe.throw(_("Please choose a plan."))

	if not frappe.db.exists("Subscription Plan", plan):
		frappe.throw(_("That plan is no longer available."))

	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		subscription = frappe.new_doc("Subscription")
		subscription.party_type = "Customer"
		subscription.party = customer
		subscription.start = start_date or frappe.utils.today()
		subscription.owner = original_user
		subscription.append("plans", {"plan": plan, "qty": 1})
		subscription.insert(ignore_permissions=True)
	finally:
		_restore_session(original_user, original_sid)

	return {"subscription": subscription.name}


@frappe.whitelist()
def get_user_display_name():
	full_name = frappe.utils.get_fullname(frappe.session.user) or frappe.session.user
	return full_name


def set_ticket_customer(doc, method=None):
	"""before_insert hook on HD Ticket. Helpdesk doesn't auto-populate the
	Customer field for tickets portal users create, so it silently stays
	blank - which then makes the ticket fall out of every customer-scoped
	filter elsewhere in this app (e.g. the dashboard's ticket counts).
	Fill it from the logged-in user's own linked HD Customer whenever it's
	missing; never overwrite a value staff/agents deliberately set.
	NOTE: HD Ticket.customer links to HD Customer, not the core Customer
	used by Project/Subscription - see get_hd_customer_for_user."""
	if doc.get("customer"):
		return
	if frappe.session.user in ("Administrator", "Guest"):
		return

	hd_customer = get_hd_customer_for_user(frappe.session.user)
	if hd_customer:
		doc.customer = hd_customer


@frappe.whitelist()
def get_dashboard_data():
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	hd_customer = get_hd_customer_for_user(frappe.session.user)

	def count(doctype, filters):
		try:
			return frappe.db.count(doctype, filters)
		except Exception:
			# Was silently returning 0 on any failure, including permission
			# errors - which is exactly what made a real "Insufficient
			# Permission for HD Ticket Status" error look like "0 closed
			# tickets" instead of surfacing as a bug. Log it instead so it's
			# visible in the Error Log if it ever happens again.
			frappe.log_error(
				title=f"Lemco Portal dashboard: count failed for {doctype}",
				message=f"filters: {filters}",
			)
			return 0

	# HD Ticket.customer links to HD Customer (a separate Helpdesk-only
	# doctype), not the core Customer that Project/Subscription below use -
	# see get_hd_customer_for_user.
	tickets_open = count("HD Ticket", {"customer": hd_customer, "status": ["not in", ["Closed", "Resolved"]]}) if hd_customer else 0
	tickets_closed = count("HD Ticket", {"customer": hd_customer, "status": ["in", ["Closed", "Resolved"]]}) if hd_customer else 0

	# NOTE: brief maps both "My Services" and "My Projects" to the same
	# /desk/project?status=Open URL - implemented as written; flag to client
	# if that duplication is intentional.
	project_base_filters = {"customer": customer}
	sub_base_filters = {"party": customer}
	if not _is_org_manager(frappe.session.user):
		project_base_filters["owner"] = frappe.session.user
		sub_base_filters["owner"] = frappe.session.user

	projects_open = count("Project", {**project_base_filters, "status": "Open"})
	projects_closed = count("Project", {**project_base_filters, "status": ["!=", "Open"]})

	subs_open = count("Subscription", {**sub_base_filters, "party_type": "Customer", "status": ["!=", "Cancelled"]})
	subs_closed = count("Subscription", {**sub_base_filters, "party_type": "Customer", "status": "Cancelled"})

	return {
		"customer": customer,
		"tickets": {"open": tickets_open, "closed": tickets_closed},
		"projects": {"open": projects_open, "closed": projects_closed},
		"subscriptions": {"open": subs_open, "closed": subs_closed},
	}


@frappe.whitelist()
def get_recent_activity(limit=8):
	"""Recent changes to this customer's own tickets/projects/subscriptions -
	not generic session login/logout events, which Activity Log is mostly
	full of and aren't useful to show a customer."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		return []
	hd_customer = get_hd_customer_for_user(frappe.session.user)

	limit = int(limit)
	events = []

	def collect(doctype, filters, label, name_field=None):
		try:
			fields = ["name", "creation", "modified"]
			if name_field:
				fields.append(name_field)
			rows = frappe.get_all(
				doctype, filters=filters, fields=fields,
				order_by="modified desc", limit_page_length=limit,
			)
		except Exception:
			return

		for r in rows:
			display = r.get(name_field) if name_field else r.name
			delta = (r.modified - r.creation).total_seconds()
			verb = "created" if delta < 5 else "updated"
			subject = f"Ticket #{display} {verb}" if label == "Ticket" else f'{label} "{display}" {verb}'
			events.append({"subject": subject, "creation": r.modified})

	if hd_customer:
		collect("HD Ticket", {"customer": hd_customer}, "Ticket")

	project_filters = {"customer": customer}
	sub_filters = {"party": customer}
	if not _is_org_manager(frappe.session.user):
		project_filters["owner"] = frappe.session.user
		sub_filters["owner"] = frappe.session.user

	collect("Project", project_filters, "Project", name_field="project_name")
	collect("Subscription", sub_filters, "Subscription")

	events.sort(key=lambda e: e["creation"], reverse=True)
	return events[:limit]


# ---------------------------------------------------------------------------
# Webinars
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def list_webinars():
	webinars = frappe.get_all(
		"Webinar",
		# status="Scheduled" already excludes Cancelled/Completed; the
		# webinar_date filter also drops ones that are Scheduled but whose
		# date has already passed (Teams doesn't always flip the status
		# once a webinar's time is over).
		filters={"status": "Scheduled", "webinar_date": [">=", today()]},
		fields=[
			"name", "title", "description", "webinar_date", "start_time",
			"duration_minutes", "language", "speaker",  "teams_join_url",
		],
		order_by="webinar_date asc, start_time asc",
	)

	# Tell the frontend which of these the logged-in customer already has a
	# Webinar Registration for, so the button can show "Registered" instead
	# of letting the click fail with the (silently-swallowed) frappe.throw
	# in register_for_webinar below.
	registered_names = set()
	if frappe.session.user != "Guest" and webinars:
		customer = get_customer_for_user(frappe.session.user)
		if customer:
			registered_names = set(
				frappe.get_all(
					"Webinar Registration",
					filters={
						"customer": customer,
						"webinar": ["in", [w["name"] for w in webinars]],
					},
					pluck="webinar",
				)
			)

	for w in webinars:
		w["registered"] = w["name"] in registered_names

	return webinars


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

	_lemco_sendmail(
		recipients=[frappe.session.user],
		subject=_("You're registered: {0}").format(webinar_doc.title),
		message=_(
			"Hi,<br><br>You're registered for <b>{0}</b> on {1}.<br>"
			'Join link: <a href="{2}">{2}</a><br><br>See you there!'
		).format(webinar_doc.title, webinar_doc.webinar_date, webinar_doc.teams_join_url or ""),
		now=True,
	)

	return {"join_url": reg.join_url, "via": "email"}


# ---------------------------------------------------------------------------
# Repair Service / RMA - customer portal side (Phase 2 of the RMA V1 spec)
#
# Company-wide visibility: unlike Projects/Subscriptions, EVERY Website User
# under a Customer sees ALL of that Customer's RMAs - there is no manager/
# member split here (spec section 4 is explicit about this; don't reuse the
# _is_org_manager() pattern from projects/subscriptions for anything here).
#
# Uses core Customer throughout (spec section 4: Website User -> Contact ->
# Customer), same as Project/Subscription - not HD Customer.
#
# Every field returned to the browser is picked explicitly, never a raw
# doc.as_dict() - spec sections 61/110 require internal fields (verified
# identification, internal diagnosis/notes, receiving photos, technician,
# stock references, etc.) to never reach a portal user, enforced here in
# Python rather than relying on client-side hiding.
#
# Simplification vs the spec's own API list (section 117): V1 here uses a
# single create+submit call (create_rma) rather than separate draft/update/
# add-item/remove-item calls - matching the create_project/create_subscription
# pattern already used elsewhere in this app. Genuine save-as-draft-and-
# resume-later support is NOT built in this pass; flag if that's needed.
# ---------------------------------------------------------------------------

RMA_CUSTOMER_STATUSES = [
	"Draft", "Awaiting Approval", "Action Required", "Approved - Awaiting Shipment",
	"Received", "Under Repair", "Awaiting Customer Approval", "Ready for Return",
	"Shipped", "Closed", "Rejected", "Cancelled",
]

RMA_CLOSED_STATUSES = ["Closed", "Rejected", "Cancelled"]


# ---------------------------------------------------------------------------
# RMA email notifications (spec sections 84-105)
#
# Reliability rules from section 105: every send goes through this one
# helper, which never lets a failure block/corrupt the calling save -
# failures are logged via frappe.log_error, not raised. frappe.sendmail
# already queues rather than sending inline, satisfying "use the Frappe
# email queue" and "avoid duplicates" (each trigger below fires exactly
# once per action, not on every save).
#
# Recipient resolution:
# - Customer emails go to whoever is resolvable for the RMA: the portal
#   user who submitted it (requested_by) if there is one, otherwise the
#   RMA's linked Contact's primary email.
# - Lemco/internal emails go to the RMA's own rma_manager if set,
#   otherwise the whole team list in RMA Settings.
#
# Every notification is gated by its matching RMA Settings toggle where
# one exists (5 toggles cover tracking/approval/ready-for-return/new-RMA/
# customer-response). Core customer-facing confirmations that have no
# matching toggle (submitted, approved, rejected, received, shipped,
# closed) are NOT gated - spec section 84 calls automated emails
# "mandatory for important workflow events", so these always fire.
# ---------------------------------------------------------------------------

def _send_rma_email(recipients, subject, message):
	recipients = [r for r in (recipients or []) if r]
	if not recipients:
		return
	try:
		_lemco_sendmail(recipients, subject, message, now=False)
	except Exception:
		frappe.log_error(title="Lemco Portal: RMA email failed", message=f"{subject} -> {recipients}")


def _rma_settings_enabled(fieldname):
	try:
		return bool(frappe.db.get_single_value("RMA Settings", fieldname))
	except Exception:
		return True


def _rma_customer_email(rma):
	if rma.requested_by:
		return rma.requested_by
	if rma.contact:
		return frappe.db.get_value("Contact", rma.contact, "email_id")
	return None


def _rma_team_recipients(rma):
	if rma.rma_manager:
		return [rma.rma_manager]
	return frappe.get_all(
		"RMA Notification Recipient",
		filters={"parent": "RMA Settings"},
		pluck="user",
	)


def _rma_portal_link(rma):
	return frappe.utils.get_url(f"/repairs/{rma.name}")


def _rma_desk_link(rma):
	return frappe.utils.get_url(f"/app/rma/{rma.name}")


def notify_rma_submitted(rma):
	"""Sections 85-86, fired once from create_rma."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"RMA {rma.name} received - Awaiting Approval"),
		_(
			"Thanks for submitting a repair request.<br><br>"
			"<b>RMA Number:</b> {0}<br>"
			"Your request is now awaiting approval - please do not ship your "
			"equipment until we confirm it's approved.<br><br>"
			"<a href=\"{1}\">View your repair request</a>"
		).format(rma.name, _rma_portal_link(rma)),
	)
	if _rma_settings_enabled("enable_new_rma_notification"):
		items_summary = "<br>".join(
			f"{row.item_code} x{row.qty} - {row.problem_description}" for row in rma.rma_items
		)
		_send_rma_email(
			_rma_team_recipients(rma),
			_(f"New RMA {rma.name} - {rma.customer}"),
			_(
				"<b>RMA:</b> {0}<br><b>Customer:</b> {1}<br><br>{2}<br><br>"
				"<a href=\"{3}\">Open in Desk</a>"
			).format(rma.name, rma.customer, items_summary, _rma_desk_link(rma)),
		)


def notify_rma_created_internally(rma):
	"""Section 87 - only if the customer actually has an email on file."""
	email = _rma_customer_email(rma)
	if not email:
		return
	_send_rma_email(
		[email],
		_(f"Lemco has created a repair request - {rma.name}"),
		_(
			"Lemco has created Repair Request {0} for equipment received / "
			"being processed.<br><br>"
			"<a href=\"{1}\">View in the Customer Portal</a> if you have portal access."
		).format(rma.name, _rma_portal_link(rma)),
	)


def notify_information_required(rma):
	"""Section 88."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"Action required on RMA {rma.name}"),
		_("<b>RMA:</b> {0}<br><br>{1}<br><br><a href=\"{2}\">Respond in the Customer Portal</a>").format(
			rma.name, rma.customer_action_message or "", _rma_portal_link(rma)
		),
	)


def notify_customer_responded(rma):
	"""Section 89 - only while the RMA is Action Required."""
	if rma.status != "Action Required":
		return
	_send_rma_email(
		_rma_team_recipients(rma),
		_(f"Customer responded on RMA {rma.name}"),
		_("<b>RMA:</b> {0}<br><b>Customer:</b> {1}<br><br><a href=\"{2}\">Open in Desk</a>").format(
			rma.name, rma.customer, _rma_desk_link(rma)
		),
	)


def notify_rma_approved(rma):
	"""Section 90."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"RMA {rma.name} approved - shipping instructions"),
		_(
			"<b>RMA:</b> {0}<br>Your repair request has been approved. Please "
			"ship the approved products to Lemco HQ following the instructions "
			"in the portal.<br><br><a href=\"{1}\">View shipping details</a>"
		).format(rma.name, _rma_portal_link(rma)),
	)


def notify_rma_rejected(rma, reason=None):
	"""Section 91."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"RMA {rma.name} was not approved"),
		_("<b>RMA:</b> {0}<br><br>{1}").format(rma.name, reason or _("Please contact us for details.")),
	)


def notify_inbound_tracking_added(rma):
	"""Section 92."""
	if not _rma_settings_enabled("enable_tracking_notification"):
		return
	_send_rma_email(
		_rma_team_recipients(rma),
		_(f"Inbound tracking added - {rma.name}"),
		_("<b>RMA:</b> {0}<br><b>Courier:</b> {1}<br><b>Tracking No.:</b> {2}").format(
			rma.name, rma.inbound_courier_vendor or "", rma.inbound_tracking_no or ""
		),
	)


def notify_equipment_received(rma):
	"""Section 93."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"RMA {rma.name} - equipment received"),
		_("<b>RMA:</b> {0}<br>We've received your equipment and repair work will begin shortly.").format(rma.name),
	)


def notify_technician_assigned(unit_technician_email, rma):
	"""Section 94."""
	_send_rma_email(
		[unit_technician_email],
		_(f"Assigned to you - {rma.name}"),
		_("<b>RMA:</b> {0}<br><b>Customer:</b> {1}<br><br><a href=\"{2}\">Open in Desk</a>").format(
			rma.name, rma.customer, _rma_desk_link(rma)
		),
	)


def notify_rma_unit_technician_change(doc, method=None):
	"""doc_events on_update hook for RMA Unit (see hooks.py). Technician
	assignment is a plain field edit, not one of the controlled actions
	in api.py's internal section - this catches it separately by
	comparing against the value before this save, and only fires when it
	actually changed to a new, non-empty technician."""
	if not doc.technician:
		return
	before = doc.get_doc_before_save()
	previous_technician = before.technician if before else None
	if doc.technician == previous_technician:
		return

	rma = frappe.get_doc("RMA", doc.rma) if frappe.db.exists("RMA", doc.rma) else None
	if not rma:
		return
	notify_technician_assigned(doc.technician, rma)


def notify_repair_approval_required(rma):
	"""Section 95."""
	if not _rma_settings_enabled("enable_approval_notification"):
		return
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"Repair approval needed - {rma.name}"),
		_("<b>RMA:</b> {0}<br>One or more items need your approval before we can continue the repair.<br><br><a href=\"{1}\">Review and respond</a>").format(
			rma.name, _rma_portal_link(rma)
		),
	)


def notify_repair_decision(rma, unit, decision):
	"""Sections 96-99 - both the customer confirmation and the internal
	notification to the RMA Manager + that unit's Technician."""
	if decision == "Approved":
		_send_rma_email(
			[_rma_customer_email(rma)],
			_(f"Repair approved - {rma.name}"),
			_("<b>RMA:</b> {0}<br><b>Item:</b> {1}<br><b>Approved amount:</b> {2}").format(
				rma.name, unit.name, unit.approved_amount_snapshot or ""
			),
		)
	else:
		_send_rma_email(
			[_rma_customer_email(rma)],
			_(f"Repair declined - {rma.name}"),
			_("<b>RMA:</b> {0}<br><b>Item:</b> {1}<br>We've recorded your decision to decline this repair.").format(
				rma.name, unit.name
			),
		)

	internal_recipients = set(_rma_team_recipients(rma))
	if unit.technician:
		internal_recipients.add(unit.technician)
	_send_rma_email(
		list(internal_recipients),
		_(f"Customer {decision.lower()} repair - {rma.name}"),
		_("<b>RMA:</b> {0}<br><b>Item:</b> {1}<br><b>Decision:</b> {2}<br><br><a href=\"{3}\">Open in Desk</a>").format(
			rma.name, unit.name, decision, _rma_desk_link(rma)
		),
	)


def notify_ready_for_return(rma):
	"""Sections 100-101."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"RMA {rma.name} ready for return"),
		_("<b>RMA:</b> {0}<br>Your repair is complete and ready to be shipped back to you. Please confirm your return address in the portal.<br><br><a href=\"{1}\">Confirm return address</a>").format(
			rma.name, _rma_portal_link(rma)
		),
	)
	if _rma_settings_enabled("enable_ready_for_return_notification"):
		_send_rma_email(
			_rma_team_recipients(rma),
			_(f"RMA {rma.name} ready for return - arrange shipment"),
			_("<b>RMA:</b> {0}<br><b>Customer:</b> {1}<br><br><a href=\"{2}\">Open in Desk</a>").format(
				rma.name, rma.customer, _rma_desk_link(rma)
			),
		)


def notify_return_address_updated(rma):
	"""Section 102."""
	_send_rma_email(
		_rma_team_recipients(rma),
		_(f"Return address updated - {rma.name}"),
		_("<b>RMA:</b> {0}<br><b>Return address:</b> {1}").format(rma.name, rma.return_address_snapshot or ""),
	)


def notify_equipment_shipped(rma):
	"""Section 103."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"RMA {rma.name} shipped"),
		_(
			"<b>RMA:</b> {0}<br><b>Courier:</b> {1}<br><b>Tracking No.:</b> {2}<br>"
			"<b>Return address:</b> {3}<br><br>"
			"Your Repair &amp; Service Report is available in the Customer Portal.<br>"
			"<a href=\"{4}\">View in the Customer Portal</a>"
		).format(
			rma.name, rma.outbound_courier_vendor or "", rma.outbound_tracking_no or _("Not available"),
			rma.return_address_snapshot or "", _rma_portal_link(rma),
		),
	)


def notify_rma_closed(rma):
	"""Section 104."""
	_send_rma_email(
		[_rma_customer_email(rma)],
		_(f"RMA {rma.name} closed"),
		_("<b>RMA:</b> {0}<br>This repair is now complete and closed. Thank you for choosing Lemco.").format(rma.name),
	)


def get_qr_code_data_uri(url):
	"""Jinja helper for the print formats (spec sections 55-70) - a QR
	code as a data URI, so print formats can embed <img src="{{...}}">
	with no external network dependency at print time. Uses the "qrcode"
	Python package; if it isn't installed on this bench, prints the raw
	URL as text instead of failing the whole print format."""
	try:
		import base64
		import io

		import qrcode

		img = qrcode.make(url)
		buf = io.BytesIO()
		img.save(buf, format="PNG")
		encoded = base64.b64encode(buf.getvalue()).decode()
		return f"data:image/png;base64,{encoded}"
	except ImportError:
		frappe.log_error(
			title="Lemco Portal: qrcode package not installed",
			message="RMA print formats need the 'qrcode' Python package (pip install qrcode) for QR images.",
		)
		return None


def get_rma_print_context(rma_name):
	"""Jinja helper - the parent RMA's customer-facing info, for print
	formats whose primary document is an RMA Unit rather than the RMA
	itself."""
	if not frappe.db.exists("RMA", rma_name):
		return {}
	rma = frappe.get_doc("RMA", rma_name)
	return {
		"name": rma.name,
		"customer": rma.customer,
		"contact": rma.contact,
		"request_date": rma.request_date,
		"customer_reference": rma.customer_reference,
	}


def get_rma_item_for_unit(rma_name, item_code):
	"""Jinja helper - best-effort match back to the RMA Item the unit was
	created from, for its customer-declared problem_description/qty/
	accessories (RMA Unit doesn't store these itself - see the module
	docstring on _create_hd_customer-adjacent RMA Unit fields). Matches
	by item_code within the parent RMA; if more than one item row shares
	that code, this returns the first match, which is an accepted
	simplification, not a guarantee of the exact originating row."""
	if not rma_name or not item_code:
		return {}
	row = frappe.db.get_value(
		"RMA Item",
		{"parent": rma_name, "item_code": item_code},
		["problem_description", "qty", "accessories"],
		as_dict=True,
	)
	return row or {}


def rma_has_permission(doc, user=None, permission_type=None):
	"""has_permission hook for RMA (see hooks.py for why this exists).
	Read-only, scoped to the caller's own Customer - never grants write."""
	user = user or frappe.session.user
	if permission_type not in (None, "read"):
		return False
	if user in ("Administrator",):
		return True
	return doc.customer == get_customer_for_user(user)


def _get_rma_for_customer(rma_name, customer):
	"""Fetches an RMA only if it belongs to the caller's own Customer - never
	reveals whether an RMA belonging to someone else exists (spec section
	109)."""
	if not frappe.db.exists("RMA", rma_name):
		frappe.throw(_("RMA not found."), frappe.DoesNotExistError)
	rma = frappe.get_doc("RMA", rma_name)
	if rma.customer != customer:
		frappe.throw(_("RMA not found."), frappe.DoesNotExistError)
	return rma


def _rma_item_customer_fields(row):
	return {
		"name": row.name,
		"item_code": row.item_code,
		"item_name": row.item_name,
		"customer_serial_lot_no": row.customer_serial_lot_no,
		"qty": row.qty,
		"problem_description": row.problem_description,
		"accessories": row.accessories,
		"approval_status": row.approval_status,
		"approved_qty": row.approved_qty,
		"photo_of_product": row.photo_of_product,
		"photo_product_label": row.photo_product_label,
		"video": row.video,
		"supporting_document": row.supporting_document,
	}


def _first_change_on(doctype, name, field, to=None):
	"""When a field was first changed to a (given/non-empty) value, read from
	the document's Version history (RMA and RMA Unit have track_changes on).
	Lets the page show real dates for steps with no date field of their own,
	e.g. "Shipped by customer", "Diagnosis completed", "Ready"."""
	import json
	try:
		versions = frappe.get_all("Version", filters={"ref_doctype": doctype, "docname": name},
			fields=["data", "creation"], order_by="creation asc")
	except Exception:
		return None
	for v in versions:
		try:
			changed = (json.loads(v.data or "{}")).get("changed") or []
		except ValueError:
			continue
		for ch in changed:
			if len(ch) >= 3 and ch[0] == field and ch[2] and (to is None or ch[2] == to):
				return v.creation
	return None


def _user_full_name(user):
	if not user:
		return None
	return frappe.db.get_value("User", user, "full_name") or user


# Stage order of the progress bar on the repair detail page.
RMA_STAGES = ["Submitted", "Approved", "Shipped", "Received", "In Repair", "Ready", "Returned"]


def _rma_progress(rma):
	"""{stages:[{label,date}], current:int}: current is the index of the active
	stage; earlier stages render as done, later ones as pending."""
	status = rma.status
	if status == "Approved - Awaiting Shipment":
		current = 2 if rma.inbound_tracking_no else 1
	elif status == "Received":
		current = 3
	elif status in ("Under Repair", "Awaiting Customer Approval"):
		current = 4
	elif status == "Ready for Return":
		current = 5
	elif status in ("Shipped", "Closed"):
		current = 6
	else:
		current = 0
	repair_started = frappe.db.sql(
		"select min(repair_started_on) from `tabRMA Unit` where rma = %s", rma.name
	)[0][0]
	shipped_by_customer = _first_change_on("RMA", rma.name, "inbound_tracking_no")
	ready_on = _first_change_on("RMA", rma.name, "status", "Ready for Return")
	dates = [rma.request_date, rma.get("approved_on"), shipped_by_customer, rma.get("received_on"), repair_started, ready_on, rma.shipped_on]
	return {
		"stages": [{"label": l, "date": d} for l, d in zip(RMA_STAGES, dates)],
		"current": current,
	}


def _rma_all_attachments(rma):
	"""Every file on the RMA (the four per-product fields) with size/date for
	the Attachments card. Each entry also carries the product's item_code."""
	out = []
	labels = {
		"photo_of_product": "Photo of product",
		"photo_product_label": "Product label",
		"video": "Video",
		"supporting_document": "Supporting document",
	}
	for row in rma.rma_items:
		for field, label in labels.items():
			url = row.get(field)
			if not url:
				continue
			meta = frappe.db.get_value("File", {"file_url": url}, ["file_name", "file_size", "creation"], as_dict=True) or {}
			out.append({
				"file_url": url,
				"file_name": meta.get("file_name") or url.rsplit("/", 1)[-1],
				"kind": label,
				"item_code": row.item_code,
				"item_row": row.name,
				"file_size": meta.get("file_size"),
				"creation": meta.get("creation"),
			})
	return out


def _rma_unit_status(unit):
	if unit.repair_decision == "Declined":
		return "Declined"
	if unit.technical_complete:
		return unit.resolution or "Repaired"
	if not unit.get("repair_started_on") and not unit.customer_visible_diagnosis:
		return "Diagnosing"
	return "In repair"


def _rma_unit_summary(unit):
	d = _rma_unit_customer_fields(unit)
	code = unit.get("source_item_code")
	d["item_code"] = code
	d["item_name"] = frappe.db.get_value("RMA Item", {"parent": unit.rma, "item_code": code}, "item_name") if code else None
	d["status"] = _rma_unit_status(unit)
	return d


def _rma_timeline(rma):
	"""Chronological events for the RMA Activity Timeline; the last one is the
	current step. [{title, date, text}]"""
	ev = [{"title": "RMA submitted", "date": rma.request_date,
		"text": "Created by Lemco." if rma.get("creation_source") == "Lemco Internal" else "Customer request."}]
	if rma.get("approved_on"):
		ev.append({"title": "RMA approved", "date": rma.approved_on, "text": "Approved by Lemco."})
	if rma.inbound_tracking_no:
		ev.append({"title": "Shipped by customer", "date": _first_change_on("RMA", rma.name, "inbound_tracking_no"),
			"text": "Tracking number {0}.".format(rma.inbound_tracking_no)})
	if rma.get("received_on"):
		n = sum(int(i.qty or 0) for i in rma.rma_items)
		ev.append({"title": "Equipment received", "date": rma.received_on, "text": "{0} units received at service center.".format(n)})
	started = frappe.db.sql("select min(repair_started_on) from `tabRMA Unit` where rma = %s", rma.name)[0][0]
	if started or rma.status in ("Under Repair", "Awaiting Customer Approval", "Ready for Return", "Shipped", "Closed"):
		ev.append({"title": "In repair", "date": started, "text": "Items are currently being repaired."})
	if rma.status in ("Ready for Return", "Shipped", "Closed"):
		ev.append({"title": "Ready for return", "date": _first_change_on("RMA", rma.name, "status", "Ready for Return"),
			"text": "Your equipment is ready for return shipment."})
	if rma.shipped_on:
		ev.append({"title": "Returned", "date": rma.shipped_on,
			"text": ("Tracking number {0}.".format(rma.outbound_tracking_no)) if rma.outbound_tracking_no else "Shipped back to you."})
	return ev


UNIT_STAGES = ["Submitted", "Approved", "Received", "Diagnosis", "Repair", "Ready", "Returned"]


def _unit_progress(unit, rma):
	g = (lambda k: unit.get(k)) if unit else (lambda k: None)
	if rma.status in ("Shipped", "Closed"):
		current = 6
	elif rma.status == "Ready for Return":
		current = 5
	elif g("technical_complete") or g("repair_started_on"):
		current = 4
	elif g("customer_visible_diagnosis"):
		current = 3
	elif g("received_date") or rma.get("received_on"):
		current = 3 if g("received_date") else 2
	else:
		current = 1 if rma.get("approved_on") else 0
	diag_on = _first_change_on("RMA Unit", unit.name, "customer_visible_diagnosis") if unit else None
	ready_on = _first_change_on("RMA", rma.name, "status", "Ready for Return")
	dates = [rma.request_date, rma.get("approved_on"), g("received_date") or rma.get("received_on"), diag_on,
		g("repair_started_on"), ready_on, rma.shipped_on]
	return {"stages": [{"label": l, "date": d} for l, d in zip(UNIT_STAGES, dates)], "current": current}


def _unit_timeline(unit, rma):
	g = (lambda k: unit.get(k)) if unit else (lambda k: None)
	ev = [{"title": "RMA submitted", "date": rma.request_date, "text": "Customer request."}]
	if rma.get("approved_on"):
		ev.append({"title": "RMA approved", "date": rma.approved_on, "text": "Approved by Lemco."})
	if rma.inbound_tracking_no and not g("received_date"):
		ev.append({"title": "Shipped by customer", "date": _first_change_on("RMA", rma.name, "inbound_tracking_no"), "text": "Tracking number {0}.".format(rma.inbound_tracking_no)})
	rec = g("received_date") or (rma.get("received_on") if not unit else None)
	if rec:
		ev.append({"title": "Received at Lemco", "date": rec, "text": "Unit received and checked in."})
	if g("customer_visible_diagnosis"):
		txt = (g("customer_visible_diagnosis") or "").strip().split("\n")[0]
		ev.append({"title": "Diagnosis completed", "date": _first_change_on("RMA Unit", unit.name, "customer_visible_diagnosis"),
			"text": txt[:140]})
	if g("repair_started_on"):
		ev.append({"title": "Repair in progress", "date": g("repair_started_on"), "text": "Technician working on the unit."})
	if g("technical_complete"):
		res = g("resolution")
		ev.append({"title": "Repair completed", "date": g("technical_completed_on") or g("repair_completed_on"),
			"text": "Unit passed final testing." if res in (None, "", "Repaired") else (res + ".")})
	return ev


def _item_status(rma, row):
	if rma.status == "Rejected" or row.approval_status == "Rejected":
		return "Rejected"
	if rma.get("received_on"):
		return "Received"
	return {"Pending": "Awaiting approval", "Approved": "Approved", "Partially Approved": "Partially approved"}.get(row.approval_status, "Awaiting approval")


def _rma_products(rma, unit_docs):
	"""One row per product for the RMA page. Before Lemco receives the
	equipment there are no RMA Units, so rows come from the RMA's own items;
	once units exist each unit gets its own row. key = what the product page
	needs in its URL (?unit=... or ?item=...)."""
	by_code = {}
	for u in unit_docs:
		by_code.setdefault(u.get("source_item_code"), []).append(u)
	out = []
	for row in rma.rma_items:
		units = by_code.pop(row.item_code, None)
		if units:
			for u in units:
				sm = _rma_unit_summary(u)
				out.append({"key": "unit=" + u.name, "item_code": row.item_code, "item_name": sm.get("item_name") or row.item_name,
					"serial": u.customer_serial_lot_no or row.customer_serial_lot_no, "problem": row.problem_description,
					"qty": 1, "status": sm["status"]})
		else:
			out.append({"key": "item=" + row.name, "item_code": row.item_code, "item_name": row.item_name,
				"serial": row.customer_serial_lot_no, "problem": row.problem_description,
				"qty": row.qty, "status": _item_status(rma, row)})
	for us in by_code.values():
		for u in us:
			sm = _rma_unit_summary(u)
			out.append({"key": "unit=" + u.name, "item_code": sm["item_code"], "item_name": sm.get("item_name"),
				"serial": u.customer_serial_lot_no, "problem": None, "qty": 1, "status": sm["status"]})
	return out


def _unit_extras(unit, rma):
	"""Fields from RMA Unit / RMA Repair Part shown on the product page, per
	the client mockup: technician, location, verified identifiers, QC result
	and customer-facing parts list (item + quantity only - never the internal
	notes or stock entry reference)."""
	parts = []
	for p in (unit.get("repair_parts") or []):
		parts.append({"item_code": p.item_code, "qty": p.quantity,
			"item_name": frappe.db.get_value("Item", p.item_code, "item_name")})
	if rma.status in ("Shipped", "Closed"):
		location = "Returned to customer"
	elif unit.get("received_date"):
		location = "Service Center"
	else:
		location = "With customer"
	actual = unit.get("actual_product")
	return {
		"technician": _user_full_name(unit.get("technician")),
		"location": location,
		"qc_result": unit.get("qc_result"),
		"parts": parts,
		"serial_display": unit.get("verified_serial_no") or unit.get("verified_lot_no") or unit.customer_serial_lot_no,
		"other_identifier": unit.get("other_identifier"),
		"actual_item_name": frappe.db.get_value("Item", actual, "item_name") if actual else None,
	}


def _rma_unit_customer_fields(unit):
	"""Customer-safe view of an RMA Unit - only what spec section 61 lists
	under "Customer Can See". Deliberately omits verified identification,
	internal diagnosis/notes and audit fields. (Technician, location, parts,
	QC result and receiving details are added for the product page only, by
	_unit_extras - per the client's mockup.)"""
	return {
		"name": unit.name,
		"customer_serial_lot_no": unit.customer_serial_lot_no,
		"service_coverage": unit.service_coverage,
		"customer_visible_diagnosis": unit.customer_visible_diagnosis,
		"customer_work_performed": unit.customer_work_performed,
		"customer_test_notes": unit.customer_test_notes,
		"resolution": unit.resolution,
		"resolution_explanation": unit.resolution_explanation,
		"customer_repair_price": unit.customer_repair_price,
		"customer_visible_comments": unit.customer_visible_comments,
		"repair_decision": unit.repair_decision,
		"technical_complete": unit.technical_complete,
	}


@frappe.whitelist()
def list_rma_products():
	"""Items eligible for the Repair Service product picker (spec section
	22) - only Items with allow_rma checked."""
	return frappe.get_all(
		"Item",
		filters={"allow_rma": 1, "disabled": 0},
		fields=["item_code", "item_name"],
		order_by="item_name asc",
	)


@frappe.whitelist()
def get_repairs_dashboard():
	"""Counts for the /repairs page tabs and the dashboard's My Repairs
	card (spec sections 7, 79). Company-wide - every user of the Customer
	sees the same numbers."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	try:
		total = frappe.db.count("RMA", {"customer": customer})
		closed = frappe.db.count("RMA", {"customer": customer, "status": ["in", RMA_CLOSED_STATUSES]})
		action_required = frappe.db.count("RMA", {"customer": customer, "customer_action_required": 1})
	except Exception:
		frappe.log_error(title="Lemco Portal: get_repairs_dashboard failed", message=customer)
		total = closed = action_required = 0

	return {
		"total": total,
		"open": total - closed,
		"closed": closed,
		"action_required": action_required,
	}


@frappe.whitelist()
def list_rmas(status=None):
	"""RMAs for the caller's whole Customer - company-wide, not owner-
	restricted (spec section 4)."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	filters = {"customer": customer}
	if status == "Open":
		filters["status"] = ["not in", RMA_CLOSED_STATUSES]
	elif status == "Closed":
		filters["status"] = ["in", RMA_CLOSED_STATUSES]
	elif status == "Action Required":
		filters["customer_action_required"] = 1
	elif status:
		filters["status"] = status

	try:
		rmas = frappe.get_all(
			"RMA",
			filters=filters,
			fields=["name", "request_date", "status", "customer_action_required", "customer_reference"],
			order_by="request_date desc",
		)
	except Exception:
		frappe.log_error(title="Lemco Portal: list_rmas failed", message=f"{customer} {filters}")
		return []

	# Attach a short product summary per RMA for the list table ("PLC-200"
	# or "4 Units" per spec section 79's example table).
	for rma in rmas:
		items = frappe.get_all("RMA Item", filters={"parent": rma["name"]}, fields=["item_name", "item_code"])
		if len(items) == 1:
			rma["products_summary"] = items[0]["item_name"] or items[0]["item_code"]
		elif len(items) > 1:
			rma["products_summary"] = f"{len(items)} Products"
		else:
			rma["products_summary"] = ""

	return rmas


@frappe.whitelist()
def get_rma(rma_name):
	"""Full customer-safe detail for one RMA - see the module docstring
	above for why every field here is picked explicitly."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	rma = _get_rma_for_customer(rma_name, customer)

	units = frappe.get_all("RMA Unit", filters={"rma": rma.name}, fields=["name"])
	unit_docs = [frappe.get_doc("RMA Unit", u["name"]) for u in units]

	messages = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "RMA", "reference_name": rma.name, "comment_type": "Comment"},
		fields=["content", "comment_email", "creation"],
		order_by="creation asc",
	)

	return {
		"name": rma.name,
		"status": rma.status,
		"request_date": rma.request_date,
		"customer_reference": rma.customer_reference,
		"customer_notes": rma.customer_notes,
		"customer_action_required": rma.customer_action_required,
		"customer_action_message": rma.customer_action_message,
		"inbound_courier_vendor": rma.inbound_courier_vendor,
		"inbound_tracking_no": rma.inbound_tracking_no,
		"return_address_type": rma.return_address_type,
		"return_address_snapshot": rma.return_address_snapshot,
		"outbound_courier_vendor": rma.outbound_courier_vendor,
		"outbound_tracking_no": rma.outbound_tracking_no,
		"shipped_on": rma.shipped_on,
		"final_report_file": rma.final_report_file,
		"items": [_rma_item_customer_fields(row) for row in rma.rma_items],
		"units": [_rma_unit_summary(u) for u in unit_docs],
		"messages": messages,
		# --- extras for the redesigned detail page ---
		"modified": rma.modified,
		"created_by": _user_full_name(rma.get("requested_by") or rma.owner),
		"responsible": _user_full_name(rma.get("rma_manager")),
		"received_on": rma.get("received_on"),
		"approved_on": rma.get("approved_on"),
		"total_units": sum(int(i.qty or 0) for i in rma.rma_items),
		"progress": _rma_progress(rma),
		"timeline": _rma_timeline(rma),
		"products": _rma_products(rma, unit_docs),
		"attachments": _rma_all_attachments(rma),
	}


@frappe.whitelist()
def create_rma(items, customer_reference=None, customer_notes=None, terms_version="v1"):
	"""Customer submits a new repair request (spec sections 8, 25, 26).
	items: JSON list of {item_code, customer_serial_lot_no, qty,
	problem_description, accessories}. Validates per spec section 25 -
	explicitly does NOT validate Serial/LOT No, which may be blank."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not items:
		frappe.throw(_("Add at least one product."))

	allowed_items = set(frappe.get_all("Item", filters={"allow_rma": 1}, pluck="name"))

	for row in items:
		if not row.get("item_code") or row["item_code"] not in allowed_items:
			frappe.throw(_("One of the selected products is not available for repair requests."))
		if not row.get("qty") or float(row["qty"]) <= 0:
			frappe.throw(_("Quantity must be greater than zero for every product."))
		if not row.get("problem_description"):
			frappe.throw(_("Please describe the problem for every product."))

	contact = frappe.db.get_value("Contact", {"email_id": frappe.session.user}, "name")
	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		rma = frappe.new_doc("RMA")
		rma.customer = customer
		rma.contact = contact
		rma.requested_by = original_user
		rma.creation_source = "Customer Portal"
		rma.status = "Awaiting Approval"
		rma.customer_reference = customer_reference
		rma.customer_notes = customer_notes
		rma.terms_accepted = 1
		rma.terms_accepted_by = original_user
		rma.terms_accepted_on = now_datetime()
		rma.terms_version = terms_version
		for row in items:
			rma.append("rma_items", {
				"item_code": row["item_code"],
				"customer_serial_lot_no": row.get("customer_serial_lot_no"),
				"qty": row["qty"],
				"problem_description": row["problem_description"],
				"accessories": row.get("accessories"),
			})
		rma.insert(ignore_permissions=True)
	finally:
		_restore_session(original_user, original_sid)

	notify_rma_submitted(rma)
	return {
		"rma": rma.name,
		"items": [
			{
				"name": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
			}
			for row in rma.rma_items
		],
	}


@frappe.whitelist()
def get_rma_unit(rma_name, unit_name=None, item_name=None):
	"""Customer-safe detail for ONE product, for the product page. Opens by
	unit_name once Lemco has received it, or by item_name (the RMA Item row)
	before that - so every product on the RMA page is clickable at any stage.
	Technician, internal notes and verified identifiers stay hidden."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	rma = _get_rma_for_customer(rma_name, customer)

	messages = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "RMA", "reference_name": rma.name, "comment_type": "Comment"},
		fields=["content", "comment_email", "creation"],
		order_by="creation asc",
	)
	common = {
		"rma": rma.name,
		"customer_reference": rma.customer_reference,
		"request_date": rma.request_date,
		"messages": messages,
	}

	if unit_name:
		if not frappe.db.exists("RMA Unit", unit_name):
			frappe.throw(_("RMA not found."), frappe.DoesNotExistError)
		unit = frappe.get_doc("RMA Unit", unit_name)
		if unit.rma != rma.name:
			frappe.throw(_("RMA not found."), frappe.DoesNotExistError)
		all_units = frappe.get_all("RMA Unit", filters={"rma": rma.name}, order_by="creation asc", pluck="name")
		data = _rma_unit_summary(unit)
		data.update(common)
		data.update({
			"unit_no": all_units.index(unit.name) + 1 if unit.name in all_units else None,
			"unit_count": len(all_units),
			"modified": unit.modified,
			"accessories_received": unit.get("accessories_received"),
			"physical_condition": unit.get("physical_condition"),
			"problem": get_rma_item_for_unit(rma.name, unit.get("source_item_code")),
			"progress": _unit_progress(unit, rma),
			"timeline": _unit_timeline(unit, rma),
			"completed_on": unit.get("technical_completed_on") or unit.get("repair_completed_on"),
			"attachments": [a for a in _rma_all_attachments(rma) if a["item_code"] == unit.get("source_item_code")],
		})
		data.update(_unit_extras(unit, rma))
		return data

	row = next((r for r in rma.rma_items if r.name == item_name), None)
	if not row:
		frappe.throw(_("RMA product not found."), frappe.DoesNotExistError)
	data = {
		"name": None, "item_code": row.item_code, "item_name": row.item_name,
		"customer_serial_lot_no": row.customer_serial_lot_no, "status": _item_status(rma, row),
		"service_coverage": None, "repair_decision": None, "technical_complete": 0,
		"customer_visible_diagnosis": None, "customer_work_performed": None, "customer_test_notes": None,
		"resolution": None, "resolution_explanation": None, "customer_repair_price": None,
		"customer_visible_comments": None,
	}
	data.update(common)
	data.update({
		"unit_no": [r.name for r in rma.rma_items].index(row.name) + 1,
		"unit_count": len(rma.rma_items),
		"modified": rma.modified,
		"accessories_received": None, "physical_condition": None,
		"problem": {"problem_description": row.problem_description, "qty": row.qty, "accessories": row.accessories},
		"qty": row.qty,
		"progress": _unit_progress(None, rma),
		"timeline": _unit_timeline(None, rma),
		"completed_on": None,
		"attachments": [a for a in _rma_all_attachments(rma) if a.get("item_row") == row.name],
		"technician": None, "location": "With customer" if not rma.get("received_on") else "Service Center",
		"qc_result": None, "parts": [], "serial_display": row.customer_serial_lot_no, "other_identifier": None, "actual_item_name": None,
	})
	return data


@frappe.whitelist()
def send_rma_message(rma_name, message):
	"""Customer message on an RMA - kept separate from Internal Notes and
	deliberately NOT a Help Desk Ticket (spec section 82). Uses Frappe's
	own comment thread rather than a bespoke messages doctype."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	if not message or not message.strip():
		frappe.throw(_("Message cannot be empty."))

	rma = _get_rma_for_customer(rma_name, customer)
	rma.add_comment(comment_type="Comment", text=message.strip())
	notify_customer_responded(rma)
	return {"ok": True}


@frappe.whitelist()
def add_inbound_tracking(rma_name, courier_vendor=None, tracking_no=None):
	"""Optional inbound tracking the customer may add once they've shipped
	their equipment (spec section 30)."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	rma = _get_rma_for_customer(rma_name, customer)
	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		if courier_vendor:
			rma.inbound_courier_vendor = courier_vendor
		if tracking_no:
			rma.inbound_tracking_no = tracking_no
		rma.save(ignore_permissions=True)
	finally:
		_restore_session(original_user, original_sid)

	notify_inbound_tracking_added(rma)
	return {"ok": True}


@frappe.whitelist()
def upload_rma_item_attachment(rma_name, rma_item, attachment_field):
	"""Customer uploads one optional attachment into one RMA Item child-row field.
	The four supported fields are stored directly on RMA Item, so the same
	structure is usable from both the Desk form and the customer portal."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	rma = _get_rma_for_customer(rma_name, customer)

	# One upload widget, four target fields: the customer picks the type, and
	# the file is stored in exactly that RMA Item field. Each type only accepts
	# its own kind of file, so a video can never land in a photo field.
	_IMG = ("jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp")
	allowed_fields = {
		"photo_of_product": ("Photo of Product", _IMG),
		"photo_product_label": ("Photo of Product Label", _IMG),
		"video": ("Video", ("mp4", "mov", "m4v", "webm", "avi", "mkv", "3gp")),
		"supporting_document": ("Supporting Document", ("pdf", "doc", "docx", "xls", "xlsx", "csv", "txt") + _IMG),
	}
	if attachment_field not in allowed_fields:
		frappe.throw(_("Invalid attachment field."))

	row = next((item for item in rma.rma_items if item.name == rma_item), None)
	if not row:
		frappe.throw(_("RMA product not found."))

	if "file" not in frappe.request.files:
		frappe.throw(_("No file uploaded."))
	uploaded = frappe.request.files["file"]
	label, exts = allowed_fields[attachment_field]
	filename = uploaded.filename or ""
	ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
	if ext not in exts:
		frappe.throw(_("\"{0}\" is not a valid file for {1}. Allowed: {2}.").format(filename, label, ", ".join(exts)))
	content = uploaded.stream.read()
	if not content:
		frappe.throw(_("The uploaded file was empty."))

	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		from frappe.utils.file_manager import save_file
		file_doc = save_file(filename, content, "RMA", rma.name, is_private=1)
		# Do not save the whole parent RMA here. A portal attachment upload
		# should only update this specific child-row field; saving the parent
		# can invoke unrelated RMA validation/workflow logic.
		frappe.db.set_value("RMA Item", row.name, attachment_field, file_doc.file_url)
		frappe.db.commit()
	finally:
		_restore_session(original_user, original_sid)

	return {"file_url": file_doc.file_url, "file_name": file_doc.file_name, "field": attachment_field, "rma_item": row.name}

@frappe.whitelist()
def approve_repair(unit_name, decision):
	"""Customer approves or declines a chargeable (Non-Warranty) repair on
	one RMA Unit (spec sections 45-48). decision: "Approved" or
	"Declined". Records a frozen snapshot of the amount/comments shown at
	the moment of decision - later price edits never change it (section
	46)."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	if decision not in ("Approved", "Declined"):
		frappe.throw(_("Invalid decision."))

	if not frappe.db.exists("RMA Unit", unit_name):
		frappe.throw(_("Not found."), frappe.DoesNotExistError)
	unit = frappe.get_doc("RMA Unit", unit_name)
	rma = _get_rma_for_customer(unit.rma, customer)

	if unit.repair_decision != "Pending":
		frappe.throw(_("A decision has already been recorded for this item."))

	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		unit.repair_decision = decision
		unit.repair_decision_by = original_user
		unit.repair_decision_on = now_datetime()
		unit.approved_amount_snapshot = unit.customer_repair_price
		unit.approval_text_snapshot = unit.customer_visible_comments
		unit.save(ignore_permissions=True)

		# If nothing else on this RMA is still waiting on a decision, drop
		# back out of the "Awaiting Customer Approval" sub-state.
		if rma.status == "Awaiting Customer Approval":
			still_pending = frappe.db.count("RMA Unit", {
				"rma": rma.name,
				"service_coverage": "Non-Warranty",
				"repair_decision": "Pending",
			})
			if not still_pending:
				rma.status = "Under Repair"
				rma.save(ignore_permissions=True)
	finally:
		_restore_session(original_user, original_sid)

	notify_repair_decision(rma, unit, decision)
	return {"ok": True}


@frappe.whitelist()
def get_customer_return_addresses():
	"""Existing ERPNext Addresses linked to the caller's Customer, for the
	return-address picker (spec section 65)."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))

	address_names = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
		pluck="parent",
	)
	if not address_names:
		return []
	return frappe.get_all(
		"Address",
		filters={"name": ["in", address_names]},
		fields=["name", "address_line1", "address_line2", "city", "pincode", "country"],
	)


@frappe.whitelist()
def set_return_address(rma_name, address_type, address_name=None, alternative_address=None):
	"""Customer confirms where a completed repair should be shipped back
	to (spec sections 65-66). Always freezes a text snapshot at
	confirmation time - the live Address may change later without
	affecting this RMA's record of where it was actually sent."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	if address_type not in ("Existing Address", "Alternative Address"):
		frappe.throw(_("Invalid address type."))

	rma = _get_rma_for_customer(rma_name, customer)

	if address_type == "Existing Address":
		if not address_name:
			frappe.throw(_("Please choose an address."))
		linked = frappe.get_all(
			"Dynamic Link",
			filters={"parent": address_name, "parenttype": "Address", "link_doctype": "Customer", "link_name": customer},
		)
		if not linked:
			frappe.throw(_("Address not found."), frappe.DoesNotExistError)
		addr = frappe.get_doc("Address", address_name)
		snapshot = ", ".join(filter(None, [addr.address_line1, addr.address_line2, addr.city, addr.pincode, addr.country]))
	else:
		if isinstance(alternative_address, str):
			alternative_address = frappe.parse_json(alternative_address)
		if not alternative_address or not alternative_address.get("address_line1"):
			frappe.throw(_("Please fill in the return address."))
		snapshot = ", ".join(filter(None, [
			alternative_address.get("recipient"),
			alternative_address.get("address_line1"),
			alternative_address.get("address_line2"),
			alternative_address.get("city"),
			alternative_address.get("postcode"),
			alternative_address.get("country"),
		]))

	original_user = frappe.session.user
	original_sid = _snapshot_session()
	frappe.set_user("Administrator")
	try:
		rma.return_address_type = address_type
		rma.return_address = address_name if address_type == "Existing Address" else None
		rma.return_address_snapshot = snapshot
		rma.save(ignore_permissions=True)
	finally:
		_restore_session(original_user, original_sid)

	notify_return_address_updated(rma)
	return {"ok": True}

# ---------------------------------------------------------------------------
# Repair Service / RMA - internal (Desk) side, Phase 3 of the RMA V1 spec.
#
# Scope decision: only the genuinely controlled, validation-gated status
# transitions in spec section 119 get a dedicated whitelisted method here
# (Approve, Reject, Receive, Ready for Return, Ship, Close, etc). Plain
# field edits - assigning a technician, entering a diagnosis, setting
# coverage, recording parts - are NOT wrapped in methods: RMA Manager and
# Repair Technician already have ordinary write permission on RMA Unit
# (granted in Phase 1), so those just use Frappe's normal Desk form save.
# Wrapping every field edit in a bespoke method would be a lot of extra
# code for zero added safety.
#
# Idempotency (spec section 120): every method re-fetches the document
# fresh and validates its CURRENT status before transitioning - never
# trusts client-passed state. A double-click after the first call already
# succeeded fails loudly on the second attempt (status has moved on)
# rather than silently repeating the action.
#
# These are Desk/System User actions, not portal ones - no
# frappe.set_user("Administrator") elevation here. Callers act as
# themselves and are gated by an explicit role check.
#
# NOT built in this pass (flagged, not silently skipped): automated
# emails for any of these triggers (spec sections 84-105), the four print
# formats (section 115), the QR access route (section 60), and the Repair
# Service workspace (section 107).
# ---------------------------------------------------------------------------

def _assert_rma_staff():
	if not set(frappe.get_roles(frappe.session.user)) & {"RMA Manager", "System Manager"}:
		frappe.throw(_("Not permitted."), frappe.PermissionError)


def _assert_rma_staff_or_technician():
	if not set(frappe.get_roles(frappe.session.user)) & {"RMA Manager", "Repair Technician", "System Manager"}:
		frappe.throw(_("Not permitted."), frappe.PermissionError)


@frappe.whitelist()
def create_customer_for_rma(customer_name, country=None, contact_name=None, email=None, phone=None):
	"""Spec section 6 - minimal Customer (+ Contact where enough info
	exists) for equipment that arrives without an existing customer.
	Creating a Website User is explicitly NOT part of this per the spec -
	that stays a separate, manual onboarding step."""
	_assert_rma_staff()
	if not customer_name:
		frappe.throw(_("Customer name is required."))

	customer = frappe.new_doc("Customer")
	customer.customer_name = customer_name
	customer.customer_type = "Company"
	if country:
		customer.territory = country
	customer.insert(ignore_permissions=True)

	contact = None
	if contact_name or email:
		contact = frappe.new_doc("Contact")
		contact.first_name = contact_name or customer_name
		if email:
			contact.append("email_ids", {"email_id": email, "is_primary": 1})
		if phone:
			contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		contact.insert(ignore_permissions=True)

	return {"customer": customer.name, "contact": contact.name if contact else None}


@frappe.whitelist()
def create_internal_rma(customer, contact=None, items=None, notes=None, already_received=1):
	"""Spec section 5.2 - Lemco creates an RMA on behalf of a customer
	(equipment arrived without prior authorization, or the customer never
	used the portal). ASSUMPTION: the common real case per the spec's own
	examples is that hardware is already physically present, so this
	defaults straight to status "Received" (skipping the normal approval-
	before-shipping step, which the spec says doesn't apply here) rather
	than "Awaiting Approval". Pass already_received=0 for the rarer case
	where that's not true yet, which leaves it in Draft for a manager to
	move forward manually."""
	_assert_rma_staff()
	if not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer not found."))

	if isinstance(items, str):
		items = frappe.parse_json(items) if items else []
	items = items or []

	rma = frappe.new_doc("RMA")
	rma.customer = customer
	rma.contact = contact
	rma.creation_source = "Lemco Internal"
	rma.rma_manager = frappe.session.user
	rma.internal_notes = notes
	rma.status = "Received" if int(already_received) else "Draft"
	if int(already_received):
		rma.received_on = now_datetime()
	for row in items:
		rma.append("rma_items", {
			"item_code": row.get("item_code"),
			"customer_serial_lot_no": row.get("customer_serial_lot_no"),
			"qty": row.get("qty") or 1,
			"problem_description": row.get("problem_description") or "",
			"accessories": row.get("accessories"),
			"approval_status": "Approved",
			"approved_qty": row.get("qty") or 1,
		})
	rma.insert(ignore_permissions=True)

	notify_rma_created_internally(rma)
	return {"rma": rma.name}


@frappe.whitelist()
def approve_rma(rma_name):
	"""Full approval - every RMA Item approved at its full requested qty."""
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status != "Awaiting Approval":
		frappe.throw(_("Only an RMA that is Awaiting Approval can be approved. Current status: {0}").format(rma.status))

	for row in rma.rma_items:
		row.approval_status = "Approved"
		row.approved_qty = row.qty

	rma.status = "Approved - Awaiting Shipment"
	rma.approved_by = frappe.session.user
	rma.approved_on = now_datetime()
	rma.save(ignore_permissions=True)
	notify_rma_approved(rma)
	return {"ok": True}


@frappe.whitelist()
def partially_approve_rma(rma_name, item_approvals):
	"""item_approvals: JSON list of {row_name, approved_qty, approval_notes}
	keyed by RMA Item child row name. Any row not included is left
	Pending - call this once with every row's decision."""
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status != "Awaiting Approval":
		frappe.throw(_("Only an RMA that is Awaiting Approval can be approved. Current status: {0}").format(rma.status))

	if isinstance(item_approvals, str):
		item_approvals = frappe.parse_json(item_approvals)
	by_row = {a["row_name"]: a for a in item_approvals}

	any_approved = False
	for row in rma.rma_items:
		decision = by_row.get(row.name)
		if not decision:
			continue
		approved_qty = float(decision.get("approved_qty") or 0)
		row.approved_qty = approved_qty
		row.approval_notes = decision.get("approval_notes")
		if approved_qty <= 0:
			row.approval_status = "Rejected"
		elif approved_qty < row.qty:
			row.approval_status = "Partially Approved"
			any_approved = True
		else:
			row.approval_status = "Approved"
			any_approved = True

	rma.status = "Approved - Awaiting Shipment" if any_approved else "Rejected"
	rma.approved_by = frappe.session.user
	rma.approved_on = now_datetime()
	rma.save(ignore_permissions=True)
	if any_approved:
		notify_rma_approved(rma)
	else:
		notify_rma_rejected(rma, reason=_("No items on this request could be approved."))
	return {"ok": True}


@frappe.whitelist()
def reject_rma(rma_name, reason=None):
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status not in ("Awaiting Approval", "Action Required"):
		frappe.throw(_("This RMA can't be rejected from its current status: {0}").format(rma.status))

	rma.status = "Rejected"
	rma.save(ignore_permissions=True)
	if reason:
		# Customer-visible reason goes into the messages thread (spec
		# section 91 requires the customer see why) rather than a new
		# dedicated field.
		rma.add_comment(comment_type="Comment", text=_("RMA rejected: {0}").format(reason))
	notify_rma_rejected(rma, reason=reason)
	return {"ok": True}


@frappe.whitelist()
def request_customer_information(rma_name, message):
	_assert_rma_staff()
	if not message:
		frappe.throw(_("A message for the customer is required."))
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status in RMA_CLOSED_STATUSES:
		frappe.throw(_("This RMA is already closed."))

	rma.status = "Action Required"
	rma.customer_action_required = 1
	rma.customer_action_message = message
	rma.save(ignore_permissions=True)
	notify_information_required(rma)
	return {"ok": True}


@frappe.whitelist()
def receive_rma(rma_name, received_items=None):
	"""Spec sections 32-34 - records physical reception and creates one
	standalone RMA Unit per received item, per received_qty (which may
	differ from the declared qty - discrepancies are recorded in
	receiving_notes on each resulting unit, never by editing the
	customer's original RMA Item declaration).
	received_items: JSON list of {row_name, received_qty, physical_condition,
	accessories_received, receiving_notes} keyed by RMA Item child row
	name. Falls back to the declared qty/item if not provided for a row."""
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status not in ("Approved - Awaiting Shipment", "Draft"):
		frappe.throw(_("This RMA isn't ready to be received. Current status: {0}").format(rma.status))

	if isinstance(received_items, str):
		received_items = frappe.parse_json(received_items) if received_items else []
	by_row = {r["row_name"]: r for r in (received_items or [])}

	created = []
	for row in rma.rma_items:
		info = by_row.get(row.name, {})
		qty = int(info.get("received_qty") or row.approved_qty or row.qty or 1)
		for _i in range(qty):
			unit = frappe.new_doc("RMA Unit")
			unit.rma = rma.name
			unit.source_item_code = row.item_code
			unit.customer_serial_lot_no = row.customer_serial_lot_no
			unit.received_date = frappe.utils.today()
			unit.received_by = frappe.session.user
			unit.physical_condition = info.get("physical_condition")
			unit.accessories_received = info.get("accessories_received")
			unit.receiving_notes = info.get("receiving_notes")
			unit.insert(ignore_permissions=True)
			created.append(unit.name)

	rma.status = "Received"
	rma.received_on = now_datetime()
	rma.save(ignore_permissions=True)
	notify_equipment_received(rma)
	return {"ok": True, "units": created}


@frappe.whitelist()
def request_customer_approval(rma_name):
	"""Explicit action moving the RMA into the customer-facing approval
	state, once a manager has set price/coverage on the relevant units.
	Requires at least one Non-Warranty unit still Pending a decision."""
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status not in ("Received", "Under Repair"):
		frappe.throw(_("Customer approval can only be requested while an RMA is under repair. Current status: {0}").format(rma.status))

	pending = frappe.db.count("RMA Unit", {"rma": rma.name, "service_coverage": "Non-Warranty", "repair_decision": "Pending"})
	if not pending:
		frappe.throw(_("No units on this RMA are waiting on a chargeable-repair decision."))

	rma.status = "Awaiting Customer Approval"
	rma.save(ignore_permissions=True)
	notify_repair_approval_required(rma)
	return {"ok": True}


@frappe.whitelist()
def complete_technical_work(unit_name):
	"""Spec section 52 - the gate before an RMA Unit can be considered
	done. Validates the required fields for the chosen resolution before
	allowing completion."""
	_assert_rma_staff_or_technician()
	unit = frappe.get_doc("RMA Unit", unit_name)
	if unit.technical_complete:
		frappe.throw(_("This unit's technical work is already marked complete."))
	if unit.repair_decision == "Pending" and unit.service_coverage == "Non-Warranty":
		frappe.throw(_("Waiting on the customer's chargeable-repair decision."))

	missing = []
	if not unit.service_coverage or unit.service_coverage == "Pending Decision":
		missing.append(_("Service Coverage"))
	if not unit.resolution:
		missing.append(_("Resolution"))
	if unit.resolution == "Repaired":
		if not unit.customer_work_performed:
			missing.append(_("Work Performed"))
		if unit.qc_result not in ("Pass", "Not Applicable"):
			missing.append(_("QC Result (must be Pass, or explicitly Not Applicable)"))
	if not unit.customer_visible_diagnosis:
		missing.append(_("Diagnosis"))

	if missing:
		frappe.throw(_("Cannot complete - missing: {0}").format(", ".join(missing)))

	unit.technical_complete = 1
	unit.technical_completed_by = frappe.session.user
	unit.technical_completed_on = now_datetime()
	if not unit.repair_completed_on:
		unit.repair_completed_on = now_datetime()
	unit.save(ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist()
def mark_ready_for_return(rma_name):
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status not in ("Under Repair", "Received"):
		frappe.throw(_("This RMA isn't in a state to be marked Ready for Return. Current status: {0}").format(rma.status))

	units = frappe.get_all("RMA Unit", filters={"rma": rma.name}, fields=["name", "technical_complete"])
	if not units:
		frappe.throw(_("This RMA has no received units yet."))
	incomplete = [u["name"] for u in units if not u["technical_complete"]]
	if incomplete:
		frappe.throw(_("These units still need Complete Technical Work: {0}").format(", ".join(incomplete)))

	rma.status = "Ready for Return"
	rma.save(ignore_permissions=True)
	notify_ready_for_return(rma)
	return {"ok": True}


@frappe.whitelist()
def ship_rma(rma_name, outbound_courier_vendor, outbound_tracking_no=None, released_by=None):
	"""Spec section 75's shipping validation checklist."""
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status != "Ready for Return":
		frappe.throw(_("This RMA must be Ready for Return before shipping. Current status: {0}").format(rma.status))
	if not rma.return_address_snapshot:
		frappe.throw(_("Return address has not been confirmed yet."))
	if not rma.final_report_file:
		frappe.throw(_("Generate the Final Customer Repair & Service Report before shipping."))
	if not outbound_courier_vendor:
		frappe.throw(_("Courier Vendor is required."))

	rma.outbound_courier_vendor = outbound_courier_vendor
	rma.outbound_tracking_no = outbound_tracking_no
	rma.released_by = released_by or frappe.session.user
	rma.shipped_on = now_datetime()
	rma.status = "Shipped"
	rma.save(ignore_permissions=True)
	notify_equipment_shipped(rma)
	return {"ok": True}


LEMCO_LETTER_HEAD = "Lemco"


@frappe.whitelist()
def setup_backend_layout():
	"""Re-lays-out the Desk RMA form and the RMA Item row editor to match the
	client mockups: numbered sections (Product & Identification, Quantity &
	Approval, Attachments, Problem Description, Additional Information), a
	"General Information" header on RMA, clearer labels and help text.

	It only adds Section/Column Break custom fields and Property Setters (the
	same things Customize Form writes) - NO data field is renamed, moved to
	another table or dropped, so existing RMAs are untouched. Works whether the
	four attachment fields are standard or custom fields. Undo any time with
	Customize Form > RMA / RMA Item > Actions > Reset to Defaults.

	Run once: bench --site <site> execute lemco_portal.api.setup_backend_layout"""
	frappe.only_for("System Manager")
	import json
	from frappe.custom.doctype.custom_field.custom_field import create_custom_field
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	def brk(dt, fieldname, fieldtype, label=None, description=None):
		if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fieldname}):
			return
		df = {"fieldname": fieldname, "fieldtype": fieldtype}
		if label:
			df["label"] = label
		if description:
			df["description"] = description
		create_custom_field(dt, df)

	def prop(dt, fieldname, prop_name, value):
		make_property_setter(dt, fieldname, prop_name, value, "Data")

	def set_order(dt, wanted):
		meta = frappe.get_meta(dt)
		existing = [d.fieldname for d in meta.fields]
		order = [f for f in wanted if f in existing] + [f for f in existing if f not in wanted]
		make_property_setter(dt, None, "field_order", json.dumps(order), "Data", for_doctype=True)

	missing = [f for f in ("photo_of_product", "photo_product_label", "video", "supporting_document")
		if not frappe.get_meta("RMA Item").has_field(f)]

	# ---------------- RMA Item (the "Editing Row" panel) ----------------
	brk("RMA Item", "lp_sec_product", "Section Break", "Product & Identification", "Select the product and provide basic identification details.")
	brk("RMA Item", "lp_col_product", "Column Break")
	brk("RMA Item", "lp_sec_qty", "Section Break", "Quantity & Approval", "Record quantity and approval status for this item.")
	brk("RMA Item", "lp_sec_att", "Section Break", "Attachments", "Add files to support this RMA item.")
	brk("RMA Item", "lp_col_att", "Column Break")
	brk("RMA Item", "lp_sec_add", "Section Break", "Additional Information", "Optional details and internal notes.")
	brk("RMA Item", "lp_col_add", "Column Break")
	set_order("RMA Item", [
		"lp_sec_product", "item_code", "item_name", "lp_col_product", "customer_serial_lot_no",
		"lp_sec_qty", "qty", "approval_status", "column_break_1", "approved_qty",
		"lp_sec_att", "photo_of_product", "video", "lp_col_att", "photo_product_label", "supporting_document",
		"section_break_1", "problem_description",
		"lp_sec_add", "accessories", "lp_col_add", "approval_notes",
	])
	prop("RMA Item", "section_break_1", "label", "Problem Description")
	prop("RMA Item", "section_break_1", "description", "Describe the issue reported by the customer.")
	for f, lbl, desc in (
		("photo_of_product", "Photo of Product", "Add a photo of the product (JPG, PNG, etc.)."),
		("photo_product_label", "Product Label", "Add product label or packaging label."),
		("video", "Video", "Add a video (MP4, MOV, etc.)."),
		("supporting_document", "Supporting Document", "Any additional supporting document (PDF, DOC, etc.)."),
	):
		if f not in missing:
			prop("RMA Item", f, "label", lbl)
			prop("RMA Item", f, "description", desc)
	prop("RMA Item", "accessories", "description", "List any accessories returned with the product.")
	prop("RMA Item", "approval_notes", "description", "Internal notes about the approval decision.")

	# ---------------- RMA (the main form) ----------------
	brk("RMA", "lp_sec_general", "Section Break", "General Information")
	existing = [d.fieldname for d in frappe.get_meta("RMA").fields if d.fieldname != "lp_sec_general"]
	set_order("RMA", ["lp_sec_general"] + existing)
	prop("RMA", "customer_reference", "label", "Customer Reference RMA No")
	prop("RMA", "rma_manager", "label", "Responsible Lemco RMA Manager")

	frappe.clear_cache(doctype="RMA")
	frappe.clear_cache(doctype="RMA Item")
	frappe.db.commit()
	return {"ok": True, "attachment_fields_not_found": missing}


@frappe.whitelist()
def setup_lemco_letter_head():
	"""One-time (safe to re-run) setup: creates/updates the "Lemco" Letter
	Head - logo in the upper-LEFT corner - and makes it the default, so every
	print format (RMA, RMA Unit, Repair & Service Report, Desk print/preview
	and the frozen PDF) carries the same header. The logo is embedded as a
	base64 image, so the PDF renderer never needs network access to load it.

	Run from Desk console:  lemco_portal.api.setup_lemco_letter_head()
	or:  bench --site <site> execute lemco_portal.api.setup_lemco_letter_head"""
	frappe.only_for("System Manager")

	import base64

	logo_path = frappe.get_app_path("lemco_portal", "public", "images", "lemco-logo.png")
	try:
		with open(logo_path, "rb") as f:
			encoded = base64.b64encode(f.read()).decode()
	except OSError:
		frappe.throw(_("Logo file not found at {0}").format(logo_path))

	content = (
		'<div style="text-align:left;margin:0 0 14px;">'
		'<img src="data:image/png;base64,' + encoded + '" alt="Lemco" '
		'style="display:block;height:60px;width:auto;">'
		"</div>"
	)

	if frappe.db.exists("Letter Head", LEMCO_LETTER_HEAD):
		doc = frappe.get_doc("Letter Head", LEMCO_LETTER_HEAD)
	else:
		doc = frappe.new_doc("Letter Head")
		doc.letter_head_name = LEMCO_LETTER_HEAD
		doc.insert(ignore_permissions=True)  # before_insert forces source=Image

	doc.source = "HTML"
	doc.content = content
	doc.align = "Left"
	doc.disabled = 0
	doc.is_default = 1
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "letter_head": doc.name}


@frappe.whitelist()
def generate_final_report(rma_name):
	"""Spec sections 71, 75, 116 - renders the "Repair & Service Report"
	print format to PDF and freezes it as an attachment. Once generated,
	re-running this OVERWRITES the frozen copy (an RMA Manager choosing to
	regenerate is a deliberate act, not automatic) - but nothing else in
	this app ever regenerates it silently, so a shipped RMA's report stays
	exactly as it was unless someone explicitly reruns this."""
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status != "Ready for Return":
		frappe.throw(_("The RMA must be Ready for Return before generating the final report."))

	html = frappe.get_print(
		"RMA",
		rma.name,
		print_format="Repair & Service Report",
		letterhead=LEMCO_LETTER_HEAD,
		no_letterhead=0,
	)
	pdf_content = frappe.utils.pdf.get_pdf(html)

	from frappe.utils.file_manager import save_file
	file_doc = save_file(f"{rma.name}-Repair-Report.pdf", pdf_content, "RMA", rma.name, is_private=1)

	rma.final_report_file = file_doc.file_url
	rma.final_report_generated_on = now_datetime()
	rma.final_report_generated_by = frappe.session.user
	rma.save(ignore_permissions=True)
	return {"ok": True, "file_url": file_doc.file_url}


@frappe.whitelist()
def download_repair_report(rma_name):
	"""Customer-facing - the frozen final report, once shipping has
	generated one. Returns nothing (not an error) if it doesn't exist yet
	rather than throwing, so the portal can just hide the download link."""
	customer = get_customer_for_user(frappe.session.user)
	if not customer:
		frappe.throw(_("No linked customer found for this account."))
	rma = _get_rma_for_customer(rma_name, customer)
	if not rma.final_report_file:
		return {"file_url": None}
	return {"file_url": rma.final_report_file}


@frappe.whitelist()
def close_rma(rma_name):
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status != "Shipped":
		frappe.throw(_("Only a Shipped RMA can be closed. Current status: {0}").format(rma.status))

	rma.status = "Closed"
	rma.closed_on = now_datetime()
	rma.save(ignore_permissions=True)
	notify_rma_closed(rma)
	return {"ok": True}


@frappe.whitelist()
def reopen_rma(rma_name, reason=None):
	"""Spec section 78 - only an RMA Manager (not a technician), and must
	be logged. Frappe's own version/comment log on the document (already
	enabled via track_changes) is the audit trail here."""
	_assert_rma_staff()
	rma = frappe.get_doc("RMA", rma_name)
	if rma.status != "Closed":
		frappe.throw(_("Only a Closed RMA can be reopened."))

	rma.status = "Under Repair"
	rma.save(ignore_permissions=True)
	rma.add_comment(comment_type="Comment", text=_("Reopened by {0}{1}").format(
		frappe.session.user, f": {reason}" if reason else ""
	))
	return {"ok": True}
    
@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_password_reset(email):
	"""
	Send a Lemco-branded password reset link.

	The response is intentionally identical whether or not the email
	exists, so the endpoint does not reveal registered email addresses.
	"""
	email = (email or "").strip().lower()

	if not email:
		frappe.throw(_("Please enter your email address."))

	# Only actual Website Users can use the Lemco portal reset flow.
	user_name = frappe.db.get_value(
		"User",
		{"name": email, "enabled": 1},
		"name",
	)

	if not user_name:
		# Do not reveal whether the email exists.
		return {
			"success": True,
			"message": _(
				"If an account exists for this email address, "
				"you will receive a password reset link shortly."
			),
		}

	user = frappe.get_doc("User", user_name)

	# Generate a raw random key.
	key = frappe.generate_hash()

	# Store only the SHA-256 hash in the database.
	user.db_set("reset_password_key", hashlib.sha256(key.encode()).hexdigest())
	#user.db_set("reset_password_key", sha256_hash(key))
	user.db_set("last_reset_password_key_generated_on", now_datetime())

	# IMPORTANT:
	# This points to our Lemco page, NOT Frappe's /update-password page.
	link = get_url("/reset-password?key=" + key)

	_send_lemco_password_reset_email(user, link)

	return {
		"success": True,
		"message": _(
			"If an account exists for this email address, "
			"you will receive a password reset link shortly."
		),
	}


def _send_lemco_password_reset_email(user, link):
	"""Send the Lemco custom password-reset email."""

	subject = _("Reset your Lemco Portal password")

	first_name = user.first_name or user.email

	message = f"""
	<p style="margin:0 0 14px;">Dear {frappe.utils.escape_html(first_name)},</p>
	<p style="margin:0 0 14px;">We received a request to reset the password for your Lemco Portal account.</p>
	<p style="margin:0 0 14px;">Click the button below to create a new password.</p>
	<p style="margin:26px 0;text-align:center;">
		<a href="{link}" style="display:inline-block;padding:13px 24px;background:#111827;color:#ffffff;text-decoration:none;border-radius:7px;font-weight:600;">Reset Password</a>
	</p>
	<p style="margin:0;color:#6b7280;font-size:13px;">If you did not request a password reset, you can safely ignore this email. Your current password will remain unchanged.</p>
	"""

	_lemco_sendmail(
		recipients=[user.email],
		subject=subject,
		message=message,
		now=True,
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def validate_password_reset_key(key):
	"""Validate a Lemco password-reset key without changing the password."""

	key = (key or "").strip()

	if not key:
		return {
			"valid": False,
			"message": _("Invalid password reset link."),
		}

	hashed_key =  hashlib.sha256(key.encode()).hexdigest()

	user_data = frappe.db.get_value(
		"User",
		{"reset_password_key": hashed_key},
		["name", "last_reset_password_key_generated_on"],
		as_dict=True,
	)

	if not user_data:
		return {
			"valid": False,
			"message": _("This password reset link is invalid or has already been used."),
		}

	expiry = frappe.db.get_single_value(
		"System Settings",
		"reset_password_link_expiry_duration",
	)

	if expiry:
		expiry = int(expiry)

		if (
			now_datetime()
			> user_data.last_reset_password_key_generated_on
			+ timedelta(seconds=expiry)
		):
			return {
				"valid": False,
				"message": _("This password reset link has expired."),
			}

	return {
		"valid": True,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def reset_password(key, new_password):
	"""Set a new password using a valid Lemco reset key."""

	key = (key or "").strip()

	if not key:
		frappe.throw(_("Invalid password reset link."))

	if not new_password:
		frappe.throw(_("Please enter a new password."))

	# Use Frappe's normal password policy.
	from frappe.core.doctype.user.user import (
		test_password_strength,
		handle_password_test_fail,
	)

	if len(new_password) > 512:
		frappe.throw(_("Password is too long."))

	result = test_password_strength(new_password)

	feedback = result.get("feedback")

	if feedback and not feedback.get("password_policy_validation_passed", False):
		handle_password_test_fail(feedback)

	hashed_key =  hashlib.sha256(key.encode()).hexdigest()

	user_data = frappe.db.get_value(
		"User",
		{"reset_password_key": hashed_key},
		["name", "last_reset_password_key_generated_on"],
		as_dict=True,
	)

	if not user_data:
		frappe.throw(
			_("This password reset link is invalid or has already been used.")
		)

	expiry = frappe.db.get_single_value(
		"System Settings",
		"reset_password_link_expiry_duration",
	)

	if expiry:
		expiry = int(expiry)

		if (
			now_datetime()
			> user_data.last_reset_password_key_generated_on
			+ timedelta(seconds=expiry)
		):
			frappe.throw(_("This password reset link has expired."))

	user = user_data.name

	# Use Frappe's standard password update mechanism.
	from frappe.core.doctype.user.user import _update_password

	logout_all_sessions = frappe.db.get_single_value(
		"System Settings",
		"logout_on_password_reset",
	) or 0

	_update_password(
		user,
		new_password,
		logout_all_sessions=int(logout_all_sessions),
	)

	# Invalidate the reset link immediately.
	frappe.db.set_value(
		"User",
		user,
		{
			"reset_password_key": "",
			"last_password_reset_date": frappe.utils.today(),
		},
	)

	frappe.clear_cache(user=user)

	return {
		"success": True,
		"message": _("Your password has been changed successfully."),
	}