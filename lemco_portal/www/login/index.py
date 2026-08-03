import frappe


def get_context(context):
	context.no_cache = 1
	context.title = "Log in — Lemco Portal"
	if frappe.session.user != "Guest":
		frappe.local.flags.redirect_location = "/dashboard"
		raise frappe.Redirect
