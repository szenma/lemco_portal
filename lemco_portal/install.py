import frappe


def after_install():
	add_portal_access_field()
	add_hd_ticket_status_read_permission()
	add_item_rma_field()
	create_rma_print_formats()
	create_repair_service_workspace()


def add_portal_access_field():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Customer": [
				{
					"fieldname": "portal_access",
					"label": "Portal Access",
					"fieldtype": "Select",
					"options": "No\nYes",
					"default": "No",
					"insert_after": "customer_name",
					"in_standard_filter": 1,
					"description": (
						"Controls whether this customer's users can log into "
						"the customer portal. Set to Yes to activate access "
						"after verifying the customer."
					),
				}
			]
		}
	)


def add_hd_ticket_status_read_permission():
	"""Portal customers need at least read access to HD Ticket Status -
	newer Helpdesk versions store ticket status as a Link to that doctype
	rather than a plain Select, so anything that filters/counts tickets by
	status (e.g. the dashboard's open/closed ticket counts) needs it.
	Without this, Frappe raises a PermissionError ("Insufficient
	Permission for HD Ticket Status") for portal users.

	Skips quietly if the doctype doesn't exist - older Helpdesk versions
	that use a plain Select field for status don't need this at all."""
	if not frappe.db.exists("DocType", "HD Ticket Status"):
		return

	from frappe.permissions import add_permission, update_permission_property

	for role in ("Customer", "HD Customer", "HD Customer Manager"):
		if not frappe.db.exists("Custom DocPerm", {"parent": "HD Ticket Status", "role": role}):
			add_permission("HD Ticket Status", role, 0)
		update_permission_property("HD Ticket Status", role, 0, "read", 1)


def add_item_rma_field():
	"""Repair Service / RMA V1 (spec section 22). Only Items with this
	checked should appear in the portal's product selector when a
	customer requests a repair - keeps freight/service/unrelated Items
	out of that picker."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "allow_rma",
					"label": "Allow RMA",
					"fieldtype": "Check",
					"insert_after": "disabled",
					"description": (
						"Only Items with this checked appear in the portal's "
						"Repair Service product selector."
					),
				}
			]
		}
	)


# ---------------------------------------------------------------------------
# Repair Service / RMA - print formats (spec section 115)
#
# Basic, functional layouts covering the content spec sections 55-70
# require - not a final visual design. Printed-document styling is the
# kind of thing worth iterating on against an actual printed test page
# rather than guessing further in code; treat these as a working first
# pass to review, not a finished design.
#
# All four are Jinja print formats (print_format_type: Jinja), using the
# jinja methods registered in hooks.py (get_qr_code_data_uri,
# get_rma_print_context, get_rma_item_for_unit) since RMA Unit doesn't
# carry its parent RMA's customer info or its originating RMA Item's
# declared problem/qty directly.
# ---------------------------------------------------------------------------

RMA_AUTHORIZATION_HTML = """
<div style="font-family:Arial,sans-serif;font-size:12px;">
	<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #1E2430;padding-bottom:10px;margin-bottom:16px;">
		<h2 style="margin:0;">LEMCO</h2>
		<h3 style="margin:0;">RMA AUTHORIZATION</h3>
	</div>
	<p><b>RMA:</b> {{ doc.name }}<br>
	<b>Customer:</b> {{ doc.customer }}<br>
	<b>Date:</b> {{ doc.approved_on or doc.request_date }}</p>

	<h4>Approved Products</h4>
	<table style="width:100%;border-collapse:collapse;" border="1" cellpadding="6">
		<tr style="background:#F1F2F5;"><th>Product</th><th>Serial/LOT No.</th><th>Approved Qty</th></tr>
		{% for row in doc.rma_items %}
			{% if row.approval_status in ("Approved", "Partially Approved") %}
			<tr>
				<td>{{ row.item_code }}</td>
				<td>{{ row.customer_serial_lot_no or "-" }}</td>
				<td>{{ row.approved_qty }}</td>
			</tr>
			{% endif %}
		{% endfor %}
	</table>

	<h4>Ship To</h4>
	<p>Lemco HQ<br>{{ frappe.db.get_single_value("RMA Settings", "lemco_hq_repair_address") or "" }}</p>

	<h4>Packing &amp; Shipping Instructions</h4>
	<div>{{ frappe.db.get_single_value("RMA Settings", "rma_instructions") or "" }}</div>

	<p style="margin-top:20px;font-style:italic;">Please write the RMA number ({{ doc.name }}) clearly on the outside of the package.</p>
</div>
"""

RMA_REPAIR_SHEET_HTML = """
<div style="font-family:Arial,sans-serif;font-size:11px;">
	{% set rma_ctx = get_rma_print_context(doc.rma) %}
	{% set item = get_rma_item_for_unit(doc.rma, doc.source_item_code) %}
	<table style="width:100%;"><tr>
		<td style="width:70%;vertical-align:top;">
			<h2 style="margin:0;">LEMCO</h2>
			<h3 style="margin:4px 0;">RMA REPAIR SHEET</h3>
			<p><b>RMA:</b> {{ doc.rma }}<br><b>UNIT:</b> {{ doc.name }}</p>
		</td>
		<td style="width:30%;text-align:right;vertical-align:top;">
			{% set qr = get_qr_code_data_uri(frappe.utils.get_url("/rma/access/" + doc.name)) %}
			{% if qr %}<img src="{{ qr }}" style="width:90px;height:90px;">{% endif %}
		</td>
	</tr></table>

	<h4>Customer Information</h4>
	<p><b>Customer:</b> {{ rma_ctx.customer }}<br>
	<b>Contact:</b> {{ rma_ctx.contact or "-" }}<br>
	<b>Customer Reference:</b> {{ rma_ctx.customer_reference or "-" }}<br>
	<b>RMA Date:</b> {{ rma_ctx.request_date }}</p>

	<h4>Customer Product Information</h4>
	<p><b>Product:</b> {{ doc.source_item_code }}<br>
	<b>Customer Serial/LOT No.:</b> {{ doc.customer_serial_lot_no or "-" }}<br>
	<b>Quantity:</b> 1 (this unit)<br>
	<b>Problem Description:</b> {{ item.problem_description or "-" }}<br>
	<b>Accessories Declared:</b> {{ item.accessories or "-" }}</p>

	<h4>Process Steps</h4>
	<table style="width:100%;">
		<tr><td>&#9633; STEP 1 - RECEPTION</td><td>&#9633; STEP 5 - REPAIR</td></tr>
		<tr><td>&#9633; STEP 2 - IDENTIFICATION &amp; EVALUATION</td><td>&#9633; STEP 6 - FINAL TEST / QC</td></tr>
		<tr><td>&#9633; STEP 3 - SERVICE COVERAGE</td><td>&#9633; STEP 7 - READY FOR RETURN</td></tr>
		<tr><td>&#9633; STEP 4 - CUSTOMER APPROVAL (if required)</td><td>&#9633; STEP 8 - SHIPPED</td></tr>
	</table>
	<p style="font-style:italic;">Checking a box here does not replace updating ERPNext.</p>

	<h4>Technical Information (current ERPNext values)</h4>
	<table style="width:100%;border-collapse:collapse;" border="1" cellpadding="5">
		<tr><td><b>Received Date</b></td><td>{{ doc.received_date or "" }}</td><td><b>Received By</b></td><td>{{ doc.received_by or "" }}</td></tr>
		<tr><td><b>Physical Condition</b></td><td colspan="3">{{ doc.physical_condition or "" }}</td></tr>
		<tr><td><b>Verified Serial No.</b></td><td>{{ doc.verified_serial_no or "" }}</td><td><b>Verified LOT No.</b></td><td>{{ doc.verified_lot_no or "" }}</td></tr>
		<tr><td><b>Technician</b></td><td>{{ doc.technician or "" }}</td><td><b>Service Coverage</b></td><td>{{ doc.service_coverage or "" }}</td></tr>
		<tr><td><b>Diagnosis</b></td><td colspan="3">{{ doc.internal_diagnosis or "" }}</td></tr>
		<tr><td><b>Repair Action</b></td><td colspan="3">{{ doc.internal_repair_notes or "" }}</td></tr>
		<tr><td><b>Parts Used</b></td><td colspan="3">{% for p in doc.repair_parts %}{{ p.item_code }} x{{ p.quantity }}{% if not loop.last %}, {% endif %}{% endfor %}</td></tr>
		<tr><td><b>Customer Approval</b></td><td>{{ doc.repair_decision or "" }}</td><td><b>QC Result</b></td><td>{{ doc.qc_result or "" }}</td></tr>
		<tr><td><b>Resolution</b></td><td colspan="3">{{ doc.resolution or "" }}</td></tr>
		<tr><td><b>Shipping Info</b></td><td colspan="3">{{ rma_ctx.name }}</td></tr>
	</table>
</div>
"""

RMA_UNIT_LABEL_HTML = """
<div style="font-family:Arial,sans-serif;font-size:11px;width:280px;border:1px solid #000;padding:8px;text-align:center;">
	{% set rma_ctx = get_rma_print_context(doc.rma) %}
	<b>LEMCO RMA</b><br>
	{{ doc.rma }}<br>
	<b>{{ doc.name.split("-")[-1] and ("UNIT-" + doc.name.split("-")[-1]) or doc.name }}</b><br>
	{{ doc.source_item_code }}<br>
	{{ rma_ctx.customer }}<br>
	{% set qr = get_qr_code_data_uri(frappe.utils.get_url("/rma/access/" + doc.name)) %}
	{% if qr %}<img src="{{ qr }}" style="width:70px;height:70px;margin-top:4px;">{% endif %}
</div>
"""

REPAIR_SERVICE_REPORT_HTML = """
<div style="font-family:Arial,sans-serif;font-size:12px;">
	<div style="border-bottom:2px solid #1E2430;padding-bottom:10px;margin-bottom:16px;">
		<h2 style="margin:0;">LEMCO</h2>
		<h3 style="margin:4px 0;">REPAIR &amp; SERVICE REPORT</h3>
	</div>
	<p><b>RMA:</b> {{ doc.name }}<br>
	<b>Customer:</b> {{ doc.customer }}<br>
	<b>Date Received:</b> {{ doc.received_on or "" }}<br>
	<b>Date Completed:</b> {{ doc.closed_on or doc.shipped_on or "" }}</p>

	{% for unit in frappe.get_all("RMA Unit", filters={"rma": doc.name}, fields=["name"]) %}
		{% set u = frappe.get_doc("RMA Unit", unit.name) %}
		{% set item = get_rma_item_for_unit(doc.name, u.source_item_code) %}
		<hr>
		<h4>{{ u.name }}</h4>
		<p><b>Product:</b> {{ u.source_item_code }}<br>
		<b>Customer Serial/LOT No.:</b> {{ u.customer_serial_lot_no or "-" }}<br>
		<b>Verified Serial/LOT No.:</b> {{ u.verified_serial_no or u.verified_lot_no or "-" }}</p>

		<p><b>Reported Issue:</b> {{ item.problem_description or "-" }}<br>
		<b>Diagnosis:</b> {{ u.customer_visible_diagnosis or "-" }}<br>
		<b>Service Coverage:</b> {{ u.service_coverage or "-" }}<br>
		<b>Work Performed:</b> {{ u.customer_work_performed or "-" }}</p>

		{% if u.repair_parts %}
		<p><b>Parts Replaced:</b><br>
		{% for p in u.repair_parts %}{{ p.quantity }} x {{ p.item_code }}<br>{% endfor %}</p>
		{% endif %}

		<p><b>Final Test:</b> {{ u.customer_test_notes or "-" }}</p>
		<p style="font-size:16px;font-weight:bold;text-align:center;border:2px solid #1E2430;padding:6px;">
			{{ (u.resolution or "").upper() }}
		</p>
	{% endfor %}

	<hr>
	{% set qr = get_qr_code_data_uri(frappe.utils.get_url("/repairs/" + doc.name)) %}
	{% if qr %}<div style="text-align:center;margin-top:16px;"><img src="{{ qr }}" style="width:80px;height:80px;"><br><small>View this repair online</small></div>{% endif %}
</div>
"""


def create_rma_print_formats():
	formats = [
		{"name": "RMA Authorization", "doc_type": "RMA", "html": RMA_AUTHORIZATION_HTML},
		{"name": "RMA Repair Sheet", "doc_type": "RMA Unit", "html": RMA_REPAIR_SHEET_HTML},
		{"name": "RMA Unit Label", "doc_type": "RMA Unit", "html": RMA_UNIT_LABEL_HTML},
		{"name": "Repair & Service Report", "doc_type": "RMA", "html": REPAIR_SERVICE_REPORT_HTML},
	]

	for fmt in formats:
		if frappe.db.exists("Print Format", fmt["name"]):
			pf = frappe.get_doc("Print Format", fmt["name"])
			pf.html = fmt["html"]
			pf.save(ignore_permissions=True)
			continue

		frappe.get_doc({
			"doctype": "Print Format",
			"name": fmt["name"],
			"doc_type": fmt["doc_type"],
			"module": "Lemco Portal",
			"print_format_type": "Jinja",
			"standard": "No",
			"disabled": 0,
			"html": fmt["html"],
		}).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Repair Service workspace (spec section 107)
#
# Frappe's Workspace "content" JSON schema is fairly version-sensitive
# (the modern block-based canvas replaced an older, simpler format a few
# releases back). This is written for the modern format. If anything
# renders oddly after deploying, Frappe's own workspace editor (the "Edit"
# button in the top right of the workspace) lets you fix layout visually
# without touching this code - worth doing that rather than debugging the
# JSON blind.
# ---------------------------------------------------------------------------

RMA_WORKSPACE_STATUS_COUNTERS = [
	"Awaiting Approval",
	"Action Required",
	"Approved - Awaiting Shipment",
	"Received",
	"Under Repair",
	"Awaiting Customer Approval",
	"Ready for Return",
	"Shipped",
	"Closed",
]


def create_repair_service_workspace():
	import json

	# One Number Card per status - a simpler, more stable building block
	# than hand-authoring the workspace canvas's own chart primitives.
	for status in RMA_WORKSPACE_STATUS_COUNTERS:
		card_name = f"RMA - {status}"
		if frappe.db.exists("Number Card", card_name):
			continue
		frappe.get_doc({
			"doctype": "Number Card",
			"name": card_name,
			"label": status,
			"document_type": "RMA",
			"function": "Count",
			"filters_json": json.dumps([["RMA", "status", "=", status]]),
			"is_public": 1,
			"module": "Lemco Portal",
		}).insert(ignore_permissions=True)

	shortcuts = [
		{"label": "RMA", "type": "DocType", "link_to": "RMA", "doc_view": "List"},
		{"label": "RMA Unit", "type": "DocType", "link_to": "RMA Unit", "doc_view": "List"},
		{"label": "RMA Settings", "type": "DocType", "link_to": "RMA Settings"},
	]

	content = [
		{"id": "header_main", "type": "header", "data": {"text": "<span class=\"h4\">Repair Service</span>", "col": 12}},
		{"id": "shortcut_rma", "type": "shortcut", "data": {"shortcut_name": "RMA", "col": 4}},
		{"id": "shortcut_unit", "type": "shortcut", "data": {"shortcut_name": "RMA Unit", "col": 4}},
		{"id": "shortcut_settings", "type": "shortcut", "data": {"shortcut_name": "RMA Settings", "col": 4}},
		{"id": "header_status", "type": "header", "data": {"text": "<span class=\"h4\">Status Overview</span>", "col": 12}},
	] + [
		{"id": f"card_{i}", "type": "number_card", "data": {"number_card_name": f"RMA - {status}", "col": 4}}
		for i, status in enumerate(RMA_WORKSPACE_STATUS_COUNTERS)
	]

	if frappe.db.exists("Workspace", "Repair Service"):
		ws = frappe.get_doc("Workspace", "Repair Service")
		ws.content = json.dumps(content)
		ws.set("shortcuts", [])
		for s in shortcuts:
			ws.append("shortcuts", s)
		ws.save(ignore_permissions=True)
		return

	# Built as a single dict (matching the Number Card/Print Format
	# pattern above) rather than frappe.new_doc() + a separate ws.name =
	# assignment - Frappe resets .name back to empty before autonaming
	# unless it was supplied as part of the original creation dict, which
	# is exactly what broke this the first time around.
	frappe.get_doc({
		"doctype": "Workspace",
		"name": "Repair Service",
		"label": "Repair Service",
		"title": "Repair Service",
		"module": "Lemco Portal",
		"public": 1,
		"is_hidden": 0,
		"icon": "tool",
		"content": json.dumps(content),
		"shortcuts": shortcuts,
	}).insert(ignore_permissions=True)