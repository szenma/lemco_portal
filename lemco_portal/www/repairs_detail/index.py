import frappe


def get_context(context):
	context.no_cache = 1
	rma_name = frappe.form_dict.get("rma_name")
	context.title = f"{rma_name or 'Repair'} — Lemco Portal"
	context.rma_name = rma_name
	if frappe.session.user == "Guest":
		redirect_to = f"/repairs/{rma_name}" if rma_name else "/repairs"
		frappe.local.flags.redirect_location = f"/login?redirect-to={redirect_to}"
		raise frappe.Redirect
