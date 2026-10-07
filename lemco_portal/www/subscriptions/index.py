import frappe


def get_context(context):
	context.no_cache = 1
	context.title = "My Subscriptions — Lemco Portal"
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/subscriptions"
		raise frappe.Redirect
