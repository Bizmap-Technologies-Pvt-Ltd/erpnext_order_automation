import json
import frappe
import requests
from frappe.utils import now_datetime
from otp_generation.api import validate_otp
from otp_generation.otp_generation.doctype.otp.otp import generate as generate_otp


def send_order_confirmation_whatsapp(order_conf):
    """
    Reusable function to send Order Confirmation details to n8n.
    Marks whatsapp_message_sent = 1 only when webhook succeeds.
    """
    try:
        payload = {
            "order_confirmation": order_conf.name,
            "phone": order_conf.contact_mobile or order_conf.contact_phone or "",
            "customer": order_conf.customer or "",
            "customer_name": order_conf.customer_name or "",
            "company": order_conf.company or "",
            "confirmation_url": order_conf.confirmation_url or "",
        }

        response = requests.post(
            "https://n8n.bizmap.in/webhook/send-confirmation-details",
            json=payload,
            timeout=10
        )

        # Treat only 200-range as success
        if 200 <= response.status_code < 300:
            if frappe.get_meta("Order Confirmation").has_field("custom_whatsapp_message_sent"):
                order_conf.db_set("custom_whatsapp_message_sent", 1, update_modified=False)

            frappe.log_error(
                f"Payload: {payload}\nStatus: {response.status_code}\nResponse: {response.text}",
                "Order Confirmation Webhook Success"
            )
            return {"status": "success", "message": "WhatsApp message sent successfully"}

        else:
            if frappe.get_meta("Order Confirmation").has_field("custom_whatsapp_message_sent"):
                order_conf.db_set("custom_whatsapp_message_sent", 0, update_modified=False)

            frappe.log_error(
                f"Payload: {payload}\nStatus: {response.status_code}\nResponse: {response.text}",
                "Order Confirmation Webhook Failed"
            )
            return {"status": "failed", "message": f"Webhook failed with status {response.status_code}"}

    except Exception:
        if frappe.get_meta("Order Confirmation").has_field("custom_whatsapp_message_sent"):
            order_conf.db_set("custom_whatsapp_message_sent", 0, update_modified=False)

        frappe.log_error(
            frappe.get_traceback(),
            "Order Confirmation Webhook Exception"
        )
        return {"status": "failed", "message": "Error while sending WhatsApp message"}


@frappe.whitelist(allow_guest=True)
def store_otp(order_id):

    order = frappe.get_doc("Order Confirmation", order_id)

    if order.verified:
        frappe.throw("Order already verified")

    # Generate OTP (external app untouched)
    otp_result = generate_otp(
        email=order.contact_email,
        phone=order.contact_mobile,
        purpose="sign_up",
    )

    # Attach reference safely
    otp_doc = frappe.get_doc("OTP", otp_result["name"])
    otp_doc.reference_doctype = "Order Confirmation"
    otp_doc.reference_docname = order.name
    otp_doc.save(ignore_permissions=True)

    # Send to n8n
    requests.post(
        "https://n8n.bizmap.in/webhook/send-otp",
        json={
            "order_id": order.name,
            "phone": order.contact_mobile,
            "otp": otp_result["otp_code"],
            "otp_name": otp_result["name"],
        },
        timeout=5
    )

    return {"status": "success", "message": "OTP sent"}




@frappe.whitelist(allow_guest=True)
def verify_order_otp(order_id, otp_code):
    order = frappe.get_doc("Order Confirmation", order_id)

    try:
        validate_otp(
            otp_code=otp_code,
            email=order.contact_email,
            phone=order.contact_mobile,
            purpose="sign_up"
        )

        return {
            "status": "success",
            "verified": True
        }

    except frappe.ValidationError as e:
        return {
            "status": "failed",
            "verified": False,
            "message": str(e)
        }



@frappe.whitelist(allow_guest=True)
def mark_order_verified():
    payload = json.loads(frappe.request.get_data(as_text=True) or "{}")
    order_id = payload.get("order_id")

    if not order_id:
        return {"status": "failed", "message": "Missing order ID"}

    order = frappe.get_doc("Order Confirmation", order_id)

    if order.verified:
        return {"status": "success", "message": "Already verified"}

    current_dt = now_datetime()

    order.verified = 1
    order.status = "Order Confirmed"
    order.custom_current_date = current_dt.date()
    order.custom_time = current_dt.time()

    order.save(ignore_permissions=True)

    if order.sales_order:
        so = frappe.get_doc("Sales Order", order.sales_order)

        if so.custom_order_confirm_via_otp != "Yes":
            so.custom_order_confirm_via_otp = "Yes"

        so.custom_current_date = current_dt.date()
        so.custom_time = current_dt.time()

        so.save(ignore_permissions=True)

    try:
        requests.post(
            "https://n8n.bizmap.in/webhook/order-confirmed-details",
            json={
                "order_confirmation": order.name,
                "customer": order.customer_name,
                "phone": order.contact_mobile,
                "company": order.company,
                "confirmation_url": order.confirmation_url
            },
            timeout=5
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Order Confirmed Webhook Failed"
        )

    frappe.db.commit()

    return {"status": "success", "message": "Order verified & confirmed"}



@frappe.whitelist()
def store_fraud_data(order_id, fraud_data):
    fraud_json = frappe.parse_json(fraud_data)

    # append as comment
    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": "Order Confirmation",
        "reference_name": order_id,
        "content": f"Fraud Data Logged:\n{frappe.as_json(fraud_json, indent=2)}"
    }).insert(ignore_permissions=True)

    frappe.db.commit()

    return {"status": "success", "message": "Fraud data stored"}


