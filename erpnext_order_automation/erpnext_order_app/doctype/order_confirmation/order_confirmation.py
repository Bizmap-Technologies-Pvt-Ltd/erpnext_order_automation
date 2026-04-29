import frappe
import requests
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from erpnext_order_automation.erpnext_order_app.order import send_order_confirmation_whatsapp


class OrderConfirmation(Document):
    pass


@frappe.whitelist()
def resend_order_confirmation_whatsapp(order_confirmation):
    order_conf = frappe.get_doc("Order Confirmation", order_confirmation)
    return send_order_confirmation_whatsapp(order_conf)


def shorten_url(long_url):
    try:
        response = requests.get(
            "https://da.gd/s",
            params={"url": long_url},
            timeout=5
        )
        if response.status_code == 200 and response.text:
            return response.text.strip()
        return long_url
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shorten URL Error")
        return long_url


@frappe.whitelist()
def send_order_confirmation(doc, method=None):
    if isinstance(doc, str):
        doc = frappe.get_doc("Sales Order", doc)

    if (
        frappe.utils.cint(doc.custom_is_unicommerce) == 1
        or doc.get("unicommerce_order_code")
    ):
        frappe.log_error(
            f"Skipped Order Confirmation for Unicommerce Sales Order: {doc.name}\n"
            f"custom_is_unicommerce: {doc.custom_is_unicommerce}\n"
            f"unicommerce_order_code: {doc.get('unicommerce_order_code')}",
            f"OC Skip EXIM - {doc.name}"
        )
        return

    existing_oc = frappe.db.get_value(
        "Order Confirmation",
        {"sales_order": doc.name},
        "name"
    )
    if existing_oc:
        return

    order_conf = get_mapped_doc(
        "Sales Order",
        doc.name,
        {
            "Sales Order": {
                "doctype": "Order Confirmation",
                "field_map": {
                    "name": "sales_order",
                    "customer": "customer",
                    "customer_name": "customer_name",
                    "company": "company",
                    "contact_mobile": "contact_mobile",
                    "contact_phone": "contact_phone",
                    "contact_email": "contact_email",
                    "grand_total": "grand_total",
                    "transaction_date": "transaction_date",
                    "total": "total",
                    "net_total": "net_total",
                    "total_taxes_and_charges": "total_taxes_and_charges",
                    "discount_amount": "discount_amount",
                    "contact_display": "contact_display",
                    "company_address_display": "company_address_display"
                }
            },
            "Sales Order Item": {
                "doctype": "Sales Order Item",
                "field_map": {
                    "item_code": "item_code",
                    "item_name": "item_name",
                    "description": "description",
                    "qty": "qty",
                    "uom": "uom",
                    "rate": "rate",
                    "amount": "amount"
                }
            }
        },
        ignore_permissions=True
    )

    if not order_conf.contact_mobile:
        order_conf.contact_mobile = doc.contact_mobile or doc.contact_phone or ""

    if not order_conf.contact_phone:
        order_conf.contact_phone = doc.contact_phone or doc.contact_mobile or ""

    order_conf.verified = 0
    if frappe.get_meta("Order Confirmation").has_field("custom_whatsapp_message_sent"):
        order_conf.custom_whatsapp_message_sent = 0

    order_conf.insert(ignore_permissions=True)

    base_url = frappe.utils.get_url().rstrip("/")
    long_url = f"{base_url}/order-confirmation/{order_conf.name}"
    short_url = shorten_url(long_url)

    if frappe.get_meta("Order Confirmation").has_field("confirmation_url"):
        order_conf.db_set("confirmation_url", short_url, update_modified=False)
        order_conf.confirmation_url = short_url

    send_order_confirmation_whatsapp(order_conf)
