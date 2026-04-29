## ERPNext Order App

Order automation app — provides OTP-verified order confirmation with WhatsApp notification via n8n webhooks.

---

### Dependencies

This app requires the following apps to be installed **before** installing `erpnext_order_automation`:

| App | Required For |
|-----|-------------|
| `frappe` | Base framework |
| `erpnext` | Sales Order, Customer, Contact doctypes |
| `otp_generation` | OTP generation and validation for order confirmation |

---

### Installation

#### 1. Install otp_generation

```bash
bench get-app https://github.com/omarrtarek29/otp_generation --branch develop
bench --site [site-name] install-app otp_generation
```

#### 2. Install this app

```bash
bench get-app https://github.com/[your-org]/erpnext_order_automation
bench --site [site-name] install-app erpnext_order_automation
```

#### 3. Configure OTP Settings

After installation, go to **OTP Settings** in the desk and configure:

| Setting | Value |
|---------|-------|
| OTP Length | 6 |
| OTP Code Type | Numeric |
| OTP Delivery Type | Email / SMS / Both (as per server config) |

The Order Confirmation OTP flow uses **Purpose: `sign_up`**.

#### 4. Configure Email / SMS

Ensure your site has Email Account or SMS settings configured so OTPs are delivered successfully.

---

### How It Works

1. On **Sales Order** submit → an **Order Confirmation** record is created automatically.
2. A short confirmation URL is generated and sent to the customer via WhatsApp (n8n webhook).
3. The customer opens the URL, requests an OTP, and verifies their order.
4. On successful OTP verification:
   - Order Confirmation is marked **Verified**.
   - Sales Order `custom_order_confirm_via_otp` is set to **Yes**.
   - Confirmation date and time are recorded on both documents.
   - A confirmed webhook is sent to n8n.

---

### License

mit
