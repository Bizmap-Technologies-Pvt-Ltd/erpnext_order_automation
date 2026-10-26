// Sales Order: "Send Order Confirmation" for submitted orders where it was skipped
frappe.ui.form.on("Sales Order", {
	onload(frm) {
		// Amend copies no_copy fields: choose again for the amended order
		if (frm.is_new() && frm.doc.amended_from && frm.doc.custom_skip_order_confirmation) {
			frm.set_value({ custom_skip_order_confirmation: 0, custom_skip_order_confirmation_reason: "" });
		}
	},

	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.custom_skip_order_confirmation) return;
		if (!frappe.user.has_role(["Sales Manager", "System Manager"])) return;

		// frappe.db.exists() in JS takes a name only; filters need get_value
		frappe.db.get_value("Order Confirmation", { sales_order: frm.doc.name }, "name").then((r) => {
			if (r.message && r.message.name) return;
			frm.add_custom_button(__("Send Order Confirmation"), () => {
				frappe.confirm(
					__("Order Confirmation was skipped for this order. Create it and send the WhatsApp link to the customer now?"),
					() =>
						frappe.call({
							method: "erpnext_order_automation.erpnext_order_app.skip_confirmation.send_skipped_order_confirmation",
							args: { sales_order: frm.doc.name },
							freeze: true,
							freeze_message: __("Sending Order Confirmation..."),
							callback: (r) => {
								if (r.exc) return;
								frappe.show_alert({
									message: __("Order Confirmation {0} sent", [r.message]),
									indicator: "green",
								});
								frm.reload_doc();
							},
						})
				);
			});
		});
	},
});
