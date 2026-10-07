// RMA Unit Desk form button. The real validation (required fields per the
// chosen resolution) lives server-side in api.py:complete_technical_work -
// this just surfaces the action and reloads on success/shows the server's
// error on failure.

frappe.ui.form.on("RMA Unit", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.technical_complete) return;

		frm.add_custom_button(__("Complete Technical Work"), () => {
			frappe.call({
				method: "lemco_portal.api.complete_technical_work",
				args: { unit_name: frm.doc.name },
				freeze: true,
				callback: () => frm.reload_doc(),
			});
		});
	},
});
