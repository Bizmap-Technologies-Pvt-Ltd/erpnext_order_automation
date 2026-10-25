"""Secure customer links for Order Confirmation.

New links carry a random, non-expiring Frappe Document Share Key instead of the
document name: /order-confirmation/<key>. Old links that use the name
(/order-confirmation/SAL-ORD-2026-00062) keep working, but always show the
masked view, because names are guessable.

The link host comes from Order Confirmation Settings > Confirmation Link Base URL,
falling back to this site's own URL.
"""

import frappe
from frappe.core.doctype.document_share_key.document_share_key import is_expired

DOCTYPE = "Order Confirmation"
ROUTE = "order-confirmation"


def get_confirmation_url(order_confirmation: str) -> str:
	doc = frappe.get_doc(DOCTYPE, order_confirmation)
	key = doc.get_document_share_key(no_expiry=True)
	return f"{get_base_url()}/{ROUTE}/{key}"


def get_base_url() -> str:
	base_url = frappe.db.get_single_value("Order Confirmation Settings", "confirmation_base_url")
	return (base_url or frappe.utils.get_url()).strip().rstrip("/")


def resolve(ref: str | None):
	"""Return (order confirmation name, opened via secure key) for a link reference."""
	ref = (ref or "").strip()
	if not ref:
		return None, False

	key = frappe.db.get_value(
		"Document Share Key",
		{"key": ref, "reference_doctype": DOCTYPE},
		["reference_docname", "expires_on"],
		as_dict=True,
	)
	if key and not is_expired(key.expires_on) and frappe.db.exists(DOCTYPE, key.reference_docname):
		return key.reference_docname, True

	# old links: document name in the URL
	if frappe.db.exists(DOCTYPE, ref):
		return ref, False

	return None, False


def get_order_confirmation(ref: str):
	"""Endpoints called by the page: accept the secure key or (old links) the name."""
	name, _via_key = resolve(ref)
	if not name:
		frappe.throw("Invalid or missing Order Confirmation.", frappe.DoesNotExistError)
	return frappe.get_doc(DOCTYPE, name)


def mask_name(value: str | None) -> str:
	parts = (value or "").split()
	return " ".join(p[:2] + "*" * max(len(p) - 2, 1) for p in parts)


def mask_email(value: str | None) -> str:
	if not value or "@" not in value:
		return ""
	local, domain = value.split("@", 1)
	return f"{local[:2]}{'*' * max(len(local) - 2, 1)}@{domain}"


def mask_phone(value: str | None) -> str:
	value = value or ""
	return "*" * max(len(value) - 4, 0) + value[-4:]
