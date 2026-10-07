// Desk polish for the RMA form (loaded globally via hooks.app_include_js).
// frappe.ui.form.on() handlers are additive, so this does not replace the
// buttons/logic already defined in public/js/rma.js.
frappe.ui.form.on("RMA", {
	refresh(frm) {
		// coloured status pill next to the title (as in the mockup)
		const colors = {
			"Draft": "gray", "Awaiting Approval": "orange", "Action Required": "orange",
			"Approved - Awaiting Shipment": "blue", "Received": "green", "Under Repair": "blue",
			"Awaiting Customer Approval": "orange", "Ready for Return": "green", "Shipped": "green",
			"Closed": "green", "Rejected": "red", "Cancelled": "gray",
		};
		if (frm.doc.status && !frm.is_new()) {
			frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status] || "gray");
		}

		// Approval Status as a coloured pill in the Products table
		const grid = frm.fields_dict.rma_items && frm.fields_dict.rma_items.grid;
		if (!grid) return;
		const df = grid.get_docfield("approval_status");
		if (df && !df._lemco_pill) {
			df._lemco_pill = true;
			const pill = {"Approved": "green", "Partially Approved": "orange", "Rejected": "red", "Pending": "gray"};
			df.formatter = function (value) {
				if (!value) return "";
				return '<span class="indicator-pill ' + (pill[value] || "gray") + '"><span>' + __(value) + "</span></span>";
			};
			grid.refresh();
		}
	},
});
