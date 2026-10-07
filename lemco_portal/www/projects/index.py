import frappe


def get_context(context):
	context.no_cache = 1
	context.title = "My Projects — Lemco Portal"
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/projects"
		raise frappe.Redirect
