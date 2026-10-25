import json
import secrets
import frappe
import requests
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime
from otp_generation.api import validate_otp
from otp_generation.otp_generation.doctype.otp.otp import generate as generate_otp

# order_id from the page: secure link key, or the document name for old links
from erpnext_order_automation.erpnext_order_app.confirmation_link import get_order_confirmation


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

            # disabled: success is not an error (failures below are still logged)
            # frappe.log_error(
            #     f"Payload: {payload}\nStatus: {response.status_code}\nResponse: {response.text}",
            #     "Order Confirmation Webhook Success"
            # )
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
@rate_limit(limit=10, seconds=60 * 60)
def store_otp(order_id):

    order = get_order_confirmation(order_id)

    if order.verified:
        frappe.throw("Order already verified")

    if not order.contact_mobile:
        frappe.throw("No WhatsApp number on this order. Please contact us to confirm your order.")

    # Generate OTP (external app untouched)
    if order.contact_email:
        otp_result = generate_otp(
            email=order.contact_email,
            phone=order.contact_mobile,
            purpose="sign_up",
        )
    else:
        # OTP Settings delivers by Email and generate_otp() requires one; the OTP goes
        # out on WhatsApp through n8n anyway, so issue it without the email copy
        otp_result = _generate_whatsapp_otp(order.contact_mobile)

    # Attach reference safely
    otp_doc = frappe.get_doc("OTP", otp_result["name"])
    otp_doc.reference_doctype = "Order Confirmation"
    otp_doc.reference_docname = order.name
    otp_doc.save(ignore_permissions=True)

    # Remember which OTP belongs to this order (OTP has no reference fields)
    frappe.cache.set_value(
        _otp_key(order.name),
        {"otp": otp_result["name"], "attempts": 0},
        expires_in_sec=OTP_TTL,
    )
    frappe.cache.delete_value(_verified_key(order.name))

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




# OLD (disabled): otp_generation's validate_otp() catches the "Invalid OTP" error and
# returns normally, so any 6 digits were accepted as verified.
# @frappe.whitelist(allow_guest=True)
# def verify_order_otp(order_id, otp_code):
#     order = frappe.get_doc("Order Confirmation", order_id)
#
#     try:
#         validate_otp(
#             otp_code=otp_code,
#             email=order.contact_email,
#             phone=order.contact_mobile,
#             purpose="sign_up"
#         )
#
#         return {
#             "status": "success",
#             "verified": True
#         }
#
#     except frappe.ValidationError as e:
#         return {
#             "status": "failed",
#             "verified": False,
#             "message": str(e)
#         }


OTP_TTL = 15 * 60  # seconds the issued OTP / successful verification is remembered
MAX_OTP_ATTEMPTS = 5


def _otp_key(order_id):
    return f"order_confirmation_otp:{order_id}"


def _verified_key(order_id):
    return f"order_confirmation_otp_verified:{order_id}"


def _generate_whatsapp_otp(phone):
    from otp_generation.otp_generation.doctype.otp.otp import get_user_otps

    settings = frappe.get_single("OTP Settings")
    if len(get_user_otps(phone=phone)) >= (settings.max_otp_attempts or 5):
        frappe.throw("You have reached the maximum number of OTP attempts. Please try again after 1 hour.")
    length = settings.otp_length or 6
    otp_code = str(secrets.randbelow(10**length)).zfill(length)
    otp_doc = frappe.get_doc(
        {
            "doctype": "OTP",
            "otp_code": otp_code,
            "phone": phone,
            "purpose": "sign_up",
            "delivery_method": "SMS",
            "status": "Valid",
        }
    ).insert(ignore_permissions=True)
    return {"otp_code": otp_code, "name": otp_doc.name}


def _failed(message):
    return {"status": "failed", "verified": False, "message": message}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60 * 60)
def verify_order_otp(order_id, otp_code):
    """Check the OTP issued for this order by store_otp (not any OTP of the contact)."""
    order = get_order_confirmation(order_id)
    if order.verified:
        return {"status": "success", "verified": True}

    issued = frappe.cache.get_value(_otp_key(order.name), expires=True)
    if not issued or not frappe.db.exists("OTP", issued["otp"]):
        return _failed("OTP expired. Please request a new OTP.")

    otp_doc = frappe.get_doc("OTP", issued["otp"])
    otp_doc.check_expiry()
    if otp_doc.status != "Valid":
        frappe.cache.delete_value(_otp_key(order.name))
        return _failed("OTP expired. Please request a new OTP.")

    if not secrets.compare_digest(str(otp_code or "").strip(), str(otp_doc.otp_code)):
        issued["attempts"] += 1
        if issued["attempts"] >= MAX_OTP_ATTEMPTS:
            otp_doc.db_set("status", "Expired", update_modified=False)
            frappe.cache.delete_value(_otp_key(order.name))
            return _failed("Too many wrong attempts. Please request a new OTP.")
        frappe.cache.set_value(_otp_key(order.name), issued, expires_in_sec=OTP_TTL)
        return _failed(f"Invalid OTP. {MAX_OTP_ATTEMPTS - issued['attempts']} attempts remaining.")

    # one-time use
    otp_doc.db_set("status", "Expired", update_modified=False)
    frappe.cache.delete_value(_otp_key(order.name))
    frappe.cache.set_value(_verified_key(order.name), 1, expires_in_sec=OTP_TTL)
    return {"status": "success", "verified": True}



@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60 * 60)
def mark_order_verified():
    payload = json.loads(frappe.request.get_data(as_text=True) or "{}")
    order_id = payload.get("order_id")

    if not order_id:
        return {"status": "failed", "message": "Missing order ID"}

    order = get_order_confirmation(order_id)

    if order.verified:
        return {"status": "success", "message": "Already verified"}

    # only after verify_order_otp succeeded for this order
    if not frappe.cache.get_value(_verified_key(order.name), expires=True):
        return {"status": "failed", "message": "Please verify the OTP first."}
    frappe.cache.delete_value(_verified_key(order.name))

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


