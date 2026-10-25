"""Customer Order Confirmation page: /order-confirmation/<secure key or name>.

Replaces the Jinja `set` lines of Web Page "orderr-conformationn", the template
itself is unchanged apart from what order-confirmation.html notes.

Personal details (name, email, phone, addresses) are masked unless the order is
verified and the page was opened with the secure key. Old links with the
document name keep working but always stay masked, because names are guessable.
"""

import frappe

from erpnext_order_automation.erpnext_order_app.confirmation_link import (
	mask_email,
	mask_name,
	mask_phone,
	resolve,
)
from erpnext_order_automation.erpnext_order_app.risk import get_shipping_country

no_cache = 1

HIDDEN_ADDRESS = "Shown after OTP verification"


def get_context(context):
	context.no_header = 1
	context.full_width = 1
	context.no_breadcrumbs = 1
	context.title = "Order Confirmation"
	context.metatags = {"robots": "noindex, nofollow"}

	ref = frappe.form_dict.get("name")
	name, via_key = resolve(ref)
	context.oc = context.doc = None
	if not name:
		return context

	oc = frappe.get_doc("Order Confirmation", name).as_dict()
	doc = frappe.get_doc("Sales Order", oc.sales_order).as_dict() if oc.sales_order else None

	contact_email = oc.contact_email or (doc.contact_email if doc else "") or ""
	contact_phone = oc.contact_mobile or oc.contact_phone or (doc.contact_phone if doc else "") or ""

	if not (oc.verified and via_key):
		contact_email = mask_email(contact_email)
		oc.customer_name = mask_name(oc.customer_name)
		oc.shipping_address = oc.address_display = HIDDEN_ADDRESS
		if doc:
			doc.customer_name = mask_name(doc.customer_name)

	context.update(
		{
			"oc": oc,
			"doc": doc,
			"order_ref": ref,
			"contact_email": contact_email,
			# the page shows contact_phone in full only on the verified view; masked_phone elsewhere
			"contact_phone": contact_phone if (oc.verified and via_key) else mask_phone(contact_phone),
			"masked_phone": mask_phone(contact_phone),
			"shipping_country": get_shipping_country(oc),
		}
	)
	return context
