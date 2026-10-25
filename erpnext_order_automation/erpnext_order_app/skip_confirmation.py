"""Skip Order Confirmation for selected Sales Orders.

A Sales Manager ticks "Skip Order Confirmation" (optionally with a reason) before
submitting a Sales Order that the client has already confirmed. On submit no
Order Confirmation is created and no WhatsApp is sent; a comment on the Sales
Order records who skipped it and why.

The fields are on permission level 1, which only Sales Manager can read and
write on Sales Order (like Shipping Terms / Port of Loading), and are not copied
to duplicated or amended orders. If they change their mind after submit, the
"Send Order Confirmation" button runs the normal flow.
"""

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

SKIP_FIELD = "custom_skip_order_confirmation"
REASON_FIELD = "custom_skip_order_confirmation_reason"
NOT_UNICOMMERCE = "!doc.custom_is_unicommerce && !doc.unicommerce_order_code"


def setup_custom_fields():
	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": SKIP_FIELD,
					"label": "Skip Order Confirmation",
					"fieldtype": "Check",
					"insert_after": "custom_order_confirm_via_otp",
					"permlevel": 1,
					"no_copy": 1,
					"in_standard_filter": 1,
					"depends_on": f"eval:{NOT_UNICOMMERCE}",
					"description": "Tick before Submit for orders the client has already confirmed: "
					"no Order Confirmation / WhatsApp will be sent.",
				},
				{
					"fieldname": REASON_FIELD,
					"label": "Skip Reason",
					"fieldtype": "Small Text",
					"insert_after": SKIP_FIELD,
					"permlevel": 1,
					"no_copy": 1,
					"depends_on": f"eval:doc.{SKIP_FIELD}",
				},
			]
		},
		update=True,
	)


def is_skipped(sales_order) -> bool:
	return bool(frappe.utils.cint(sales_order.get(SKIP_FIELD)))


def log_skip(sales_order):
	reason = (sales_order.get(REASON_FIELD) or "").strip()
	sales_order.add_comment(
		"Comment",
		_("Order Confirmation skipped by {0}.").format(frappe.session.user)
		+ (" " + _("Reason: {0}").format(frappe.utils.escape_html(reason)) if reason else ""),
	)


@frappe.whitelist()
def send_skipped_order_confirmation(sales_order: str):
	"""Fallback: create and send the Order Confirmation for a skipped, submitted Sales Order."""
	from erpnext_order_automation.erpnext_order_app.doctype.order_confirmation.order_confirmation import (
		send_order_confirmation,
	)

	frappe.only_for(["Sales Manager", "System Manager"])

	so = frappe.get_doc("Sales Order", sales_order)
	so.check_permission("read")
	if so.docstatus != 1:
		frappe.throw(_("Sales Order {0} is not submitted.").format(so.name))
	if not is_skipped(so):
		frappe.throw(_("Order Confirmation was not skipped for {0}.").format(so.name))
	if frappe.db.exists("Order Confirmation", {"sales_order": so.name}):
		frappe.throw(_("An Order Confirmation already exists for {0}.").format(so.name))

	frappe.flags.force_order_confirmation = True
	try:
		send_order_confirmation(so)
	finally:
		frappe.flags.force_order_confirmation = False

	so.add_comment("Comment", _("Order Confirmation sent by {0} after it was skipped.").format(frappe.session.user))
	return frappe.db.get_value("Order Confirmation", {"sales_order": so.name}, "name")
