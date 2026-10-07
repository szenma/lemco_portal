// "Create RMA (Internal)" button on the RMA list view - the entry point
// for spec sections 5.2/6/31: equipment arrives at Lemco without an
// existing RMA, sometimes without an existing Customer either.

frappe.listview_settings["RMA"] = frappe.listview_settings["RMA"] || {};

(function () {
	const original_onload = frappe.listview_settings["RMA"].onload;

	frappe.listview_settings["RMA"].onload = function (listview) {
		if (original_onload) original_onload(listview);

		listview.page.add_inner_button(__("Create RMA (Internal)"), () => {
			open_internal_rma_dialog(listview);
		});
	};
})();

function open_internal_rma_dialog(listview) {
	const dialog = new frappe.ui.Dialog({
		title: __("Create RMA (Internal)"),
		fields: [
			{ fieldname: "new_customer", fieldtype: "Check", label: __("Customer does not exist yet") },
			{ fieldname: "customer", fieldtype: "Link", options: "Customer", label: __("Customer"), reqd: 1, depends_on: "eval:!doc.new_customer" },
			{ fieldname: "customer_name", fieldtype: "Data", label: __("Customer / Company Name"), depends_on: "eval:doc.new_customer" },
			{ fieldname: "country", fieldtype: "Link", options: "Country", label: __("Country"), depends_on: "eval:doc.new_customer" },
			{ fieldname: "contact_name", fieldtype: "Data", label: __("Contact Name"), depends_on: "eval:doc.new_customer" },
			{ fieldname: "email", fieldtype: "Data", label: __("Email"), depends_on: "eval:doc.new_customer" },
			{ fieldname: "phone", fieldtype: "Data", label: __("Telephone"), depends_on: "eval:doc.new_customer" },
			{ fieldname: "section_break_1", fieldtype: "Section Break", label: __("Equipment") },
			{ fieldname: "already_received", fieldtype: "Check", label: __("Equipment is already physically at Lemco"), default: 1,
				description: __("If checked, this RMA is created directly as Received - skipping the normal approval-before-shipping step, per spec section 5.2.") },
			{ fieldname: "items", fieldtype: "Table",
				fields: [
					{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Product"), in_list_view: 1, reqd: 1 },
					{ fieldname: "customer_serial_lot_no", fieldtype: "Data", label: __("Serial/LOT No."), in_list_view: 1 },
					{ fieldname: "qty", fieldtype: "Float", label: __("Qty"), in_list_view: 1, default: 1 },
					{ fieldname: "problem_description", fieldtype: "Small Text", label: __("Problem"), in_list_view: 1 },
				],
			},
			{ fieldname: "notes", fieldtype: "Small Text", label: __("Internal Notes") },
		],
		primary_action_label: __("Create RMA"),
		primary_action(values) {
			create(values, dialog, listview);
		},
	});
	dialog.show();
}

function create(values, dialog, listview) {
	if (values.new_customer) {
		frappe.call({
			method: "lemco_portal.api.create_customer_for_rma",
			args: {
				customer_name: values.customer_name,
				country: values.country,
				contact_name: values.contact_name,
				email: values.email,
				phone: values.phone,
			},
			freeze: true,
			callback: (r) => {
				if (!r.message) return;
				create_rma_with_customer(r.message.customer, r.message.contact, values, dialog, listview);
			},
		});
	} else {
		create_rma_with_customer(values.customer, null, values, dialog, listview);
	}
}

function create_rma_with_customer(customer, contact, values, dialog, listview) {
	frappe.call({
		method: "lemco_portal.api.create_internal_rma",
		args: {
			customer,
			contact,
			items: JSON.stringify(values.items || []),
			notes: values.notes,
			already_received: values.already_received ? 1 : 0,
		},
		freeze: true,
		callback: (r) => {
			dialog.hide();
			if (r.message && r.message.rma) {
				frappe.set_route("Form", "RMA", r.message.rma);
			} else {
				listview.refresh();
			}
		},
	});
}
