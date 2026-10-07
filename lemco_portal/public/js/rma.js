// RMA Desk form buttons. Each button only appears when the document's
// current status makes that action valid - the same checks are re-run
// server-side in api.py, this is just so staff aren't shown actions that
// would immediately fail.

frappe.ui.form.on("RMA", {
	refresh(frm) {
		if (frm.is_new()) return;
		const status = frm.doc.status;

		if (status === "Awaiting Approval") {
			frm.add_custom_button(__("Approve (Full)"), () => {
				frappe.confirm(__("Approve every item at its full requested quantity?"), () => {
					call(frm, "lemco_portal.api.approve_rma", { rma_name: frm.doc.name });
				});
			}, __("Actions"));

			frm.add_custom_button(__("Partially Approve..."), () => {
				open_partial_approval_dialog(frm);
			}, __("Actions"));

			frm.add_custom_button(__("Reject..."), () => {
				frappe.prompt(
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (shown to the customer)") },
					(values) => call(frm, "lemco_portal.api.reject_rma", { rma_name: frm.doc.name, reason: values.reason }),
					__("Reject RMA")
				);
			}, __("Actions"));

			frm.add_custom_button(__("Request Information..."), () => {
				frappe.prompt(
					{ fieldname: "message", fieldtype: "Small Text", label: __("Message for the customer"), reqd: 1 },
					(values) => call(frm, "lemco_portal.api.request_customer_information", { rma_name: frm.doc.name, message: values.message }),
					__("Request Information")
				);
			}, __("Actions"));
		}

		if (status === "Action Required") {
			frm.add_custom_button(__("Reject..."), () => {
				frappe.prompt(
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (shown to the customer)") },
					(values) => call(frm, "lemco_portal.api.reject_rma", { rma_name: frm.doc.name, reason: values.reason }),
					__("Reject RMA")
				);
			}, __("Actions"));
		}

		if (status === "Approved - Awaiting Shipment" || status === "Draft") {
			frm.add_custom_button(__("Receive Products"), () => {
				open_receive_dialog(frm);
			}, __("Actions"));
		}

		if (status === "Received" || status === "Under Repair") {
			frm.add_custom_button(__("Request Customer Approval"), () => {
				call(frm, "lemco_portal.api.request_customer_approval", { rma_name: frm.doc.name });
			}, __("Actions"));

			frm.add_custom_button(__("Mark Ready for Return"), () => {
				call(frm, "lemco_portal.api.mark_ready_for_return", { rma_name: frm.doc.name });
			}, __("Actions"));
		}

		if (status === "Ready for Return") {
			frm.add_custom_button(__("Generate Final Report"), () => {
				call(frm, "lemco_portal.api.generate_final_report", { rma_name: frm.doc.name });
			}, __("Actions"));

			frm.add_custom_button(__("Ship..."), () => {
				frappe.prompt(
					[
						{ fieldname: "outbound_courier_vendor", fieldtype: "Data", label: __("Courier Vendor"), reqd: 1 },
						{ fieldname: "outbound_tracking_no", fieldtype: "Data", label: __("Tracking No.") },
						{ fieldname: "released_by", fieldtype: "Link", options: "User", label: __("Released By"), default: frappe.session.user },
					],
					(values) => call(frm, "lemco_portal.api.ship_rma", { rma_name: frm.doc.name, ...values }),
					__("Ship RMA")
				);
			}, __("Actions"));

			if (!frm.doc.return_address_snapshot) {
				frm.dashboard.add_comment(__("Return address not yet confirmed by the customer - Ship is not available until it is."), "orange", true);
			}
			if (!frm.doc.final_report_file) {
				frm.dashboard.add_comment(__("Final report not yet generated - Ship is not available until it is."), "orange", true);
			}
		}

		if (status === "Shipped") {
			frm.add_custom_button(__("Close"), () => {
				call(frm, "lemco_portal.api.close_rma", { rma_name: frm.doc.name });
			}, __("Actions"));
		}

		if (status === "Closed" && frappe.user.has_role("RMA Manager")) {
			frm.add_custom_button(__("Reopen..."), () => {
				frappe.prompt(
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (logged on the RMA)") },
					(values) => call(frm, "lemco_portal.api.reopen_rma", { rma_name: frm.doc.name, reason: values.reason }),
					__("Reopen RMA")
				);
			}, __("Actions"));
		}
	},
});

function call(frm, method, args) {
	frappe.call({
		method,
		args,
		freeze: true,
		callback: () => frm.reload_doc(),
	});
}

function open_partial_approval_dialog(frm) {
	const fields = (frm.doc.rma_items || []).map((row) => ({
		fieldtype: "Section Break",
		label: `${row.item_code} (requested ${row.qty})`,
	})).flatMap((section, i) => {
		const row = frm.doc.rma_items[i];
		return [
			section,
			{ fieldname: `approved_qty_${row.name}`, fieldtype: "Float", label: __("Approved Qty"), default: row.qty },
			{ fieldname: `approval_notes_${row.name}`, fieldtype: "Small Text", label: __("Notes") },
		];
	});

	const dialog = new frappe.ui.Dialog({
		title: __("Partially Approve RMA"),
		fields,
		primary_action_label: __("Save"),
		primary_action(values) {
			const item_approvals = frm.doc.rma_items.map((row) => ({
				row_name: row.name,
				approved_qty: values[`approved_qty_${row.name}`],
				approval_notes: values[`approval_notes_${row.name}`],
			}));
			frappe.call({
				method: "lemco_portal.api.partially_approve_rma",
				args: { rma_name: frm.doc.name, item_approvals: JSON.stringify(item_approvals) },
				freeze: true,
				callback: () => {
					dialog.hide();
					frm.reload_doc();
				},
			});
		},
	});
	dialog.show();
}

function open_receive_dialog(frm) {
	const fields = (frm.doc.rma_items || []).flatMap((row) => [
		{ fieldtype: "Section Break", label: `${row.item_code} (declared qty ${row.qty})` },
		{ fieldname: `received_qty_${row.name}`, fieldtype: "Int", label: __("Received Qty"), default: row.approved_qty || row.qty },
		{ fieldname: `physical_condition_${row.name}`, fieldtype: "Small Text", label: __("Physical Condition") },
		{ fieldname: `accessories_received_${row.name}`, fieldtype: "Data", label: __("Accessories Received") },
		{ fieldname: `receiving_notes_${row.name}`, fieldtype: "Small Text", label: __("Receiving Notes (record any discrepancy here)") },
	]);

	const dialog = new frappe.ui.Dialog({
		title: __("Receive Products"),
		fields,
		primary_action_label: __("Receive & Create Units"),
		primary_action(values) {
			const received_items = frm.doc.rma_items.map((row) => ({
				row_name: row.name,
				received_qty: values[`received_qty_${row.name}`],
				physical_condition: values[`physical_condition_${row.name}`],
				accessories_received: values[`accessories_received_${row.name}`],
				receiving_notes: values[`receiving_notes_${row.name}`],
			}));
			frappe.call({
				method: "lemco_portal.api.receive_rma",
				args: { rma_name: frm.doc.name, received_items: JSON.stringify(received_items) },
				freeze: true,
				callback: (r) => {
					dialog.hide();
					frm.reload_doc();
					if (r.message && r.message.units) {
						frappe.msgprint(__("Created units: {0}", [r.message.units.join(", ")]));
					}
				},
			});
		},
	});
	dialog.show();
}
