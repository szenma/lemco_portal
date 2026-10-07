import frappe

# Spec sections 58-60: the QR code printed on each RMA Unit's Repair Sheet
# and label points here. This route is a navigation shortcut only - it
# NEVER grants authentication or bypasses permissions on its own. It just
# figures out who's looking and sends them to the right place:
#   - not logged in            -> normal login, then come back here
#   - Lemco employee            -> the internal, editable RMA Unit Desk form
#   - authorized portal customer -> the customer-safe /repairs/<rma> page
#   - anyone else               -> a plain "Access denied", no leaked detail
#
# Deliberately reuses get_customer_for_user() from api.py rather than
# duplicating that lookup - same authorization logic as everywhere else
# in the app.

INTERNAL_ROLES = {"RMA Manager", "Repair Technician", "System Manager"}


def get_context(context):
	context.no_cache = 1
	context.title = "RMA Access"
	context.access_denied = False

	unit_name = frappe.form_dict.get("unit_name")
	if not unit_name:
		context.access_denied = True
		return

	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = f"/login?redirect-to=/rma/access/{unit_name}"
		raise frappe.Redirect

	if set(frappe.get_roles(frappe.session.user)) & INTERNAL_ROLES:
		frappe.local.flags.redirect_location = f"/app/rma-unit/{unit_name}"
		raise frappe.Redirect

	if not frappe.db.exists("RMA Unit", unit_name):
		context.access_denied = True
		return

	rma_name = frappe.db.get_value("RMA Unit", unit_name, "rma")
	rma_customer = frappe.db.get_value("RMA", rma_name, "customer") if rma_name else None

	from lemco_portal.api import get_customer_for_user

	customer = get_customer_for_user(frappe.session.user)
	if customer and rma_customer and customer == rma_customer:
		frappe.local.flags.redirect_location = f"/repairs/{rma_name}"
		raise frappe.Redirect

	context.access_denied = True
