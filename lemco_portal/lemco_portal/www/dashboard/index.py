import frappe
from lemco_portal.api import customer_has_portal_access, get_customer_for_user


def get_context(context):
	context.no_cache = 1
	context.title = "Dashboard — Lemco Portal"

	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login"
		raise frappe.Redirect

	customer = get_customer_for_user(frappe.session.user)
	context.customer = customer
	context.pending_approval = not customer_has_portal_access(customer)
