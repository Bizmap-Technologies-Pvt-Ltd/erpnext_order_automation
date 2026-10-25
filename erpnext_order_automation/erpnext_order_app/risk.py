"""Server-side risk check for the customer Order Confirmation page.

The page used to look up the visitor's IP with ipapi.co from the browser and
score the risk there (rate limited, and anyone could fake the result), and the
result was only stored for logged-in users. Now the server takes the real
request IP, looks it up in free offline data and stores the result on the
Order Confirmation:

- DB-IP Lite city + ASN databases (CC BY 4.0, attribution shown on the page)
- X4BNet lists_vpn VPN and datacenter IPv4 lists (MIT)

update_risk_data() downloads them (monthly scheduler job). If the data is
missing the page still works, the location just shows as unknown.
"""

import bisect
import gzip
import ipaddress
import json
import os
import shutil
import tempfile

import frappe
import requests
from frappe.rate_limiter import rate_limit
from frappe.utils import add_months, cint, flt, getdate, now_datetime

from erpnext_order_automation.erpnext_order_app.confirmation_link import get_order_confirmation

DBIP_URL = "https://download.db-ip.com/free/dbip-{kind}-lite-{month}.mmdb.gz"
VPN_LIST_URL = "https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/{kind}/ipv4.txt"
FILES = {
	"city": "dbip-city-lite.mmdb",
	"asn": "dbip-asn-lite.mmdb",
	"vpn": "vpn-ipv4.txt",
	"datacenter": "datacenter-ipv4.txt",
}

HIGH_VALUE_ORDER = 10000
# same weights / levels the page used before
RISK_POINTS = {"country_mismatch": 40, "timezone_mismatch": 20, "vpn": 50, "datacenter": 30, "high_value": 10}
HIGH_RISK, MEDIUM_RISK = 80, 40

_cache = {}  # per process: path -> (mtime, loaded data)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60 * 60)
def log_page_visit(order_id, device=None):
	"""Called by the page on load: assess the visitor and store the result."""
	order = get_order_confirmation(order_id)
	device = frappe.parse_json(device) if isinstance(device, str) else (device or {})
	device = {k: str(v)[:300] for k, v in device.items() if not isinstance(v, dict | list)}

	result = assess(order, frappe.local.request_ip, device)
	_store(order, result, device)
	frappe.db.commit()
	return result


def assess(order, ip, device):
	geo = lookup_ip(ip)
	shipping_country = get_shipping_country(order)
	browser_tz = device.get("timezone") or ""

	country_match = None
	timezone_match = None
	if geo.get("countryCode"):
		country_match = geo["countryCode"] == shipping_country
		import pytz

		timezone_match = browser_tz in pytz.country_timezones.get(geo["countryCode"], [])

	signals = {
		"geoLocationMatch": country_match,
		"timezoneMatch": timezone_match,
		"vpnProxy": in_list("vpn", ip),
		"datacenter": in_list("datacenter", ip),
		"orderValueThreshold": flt(order.grand_total) > HIGH_VALUE_ORDER,
	}

	points = 0
	if country_match is False:
		points += RISK_POINTS["country_mismatch"]
	if timezone_match is False:
		points += RISK_POINTS["timezone_mismatch"]
	if signals["vpnProxy"]:
		points += RISK_POINTS["vpn"]
	elif signals["datacenter"]:
		points += RISK_POINTS["datacenter"]
	if signals["orderValueThreshold"]:
		points += RISK_POINTS["high_value"]

	score = "HIGH" if points >= HIGH_RISK else "MEDIUM" if points >= MEDIUM_RISK else "LOW"
	return {
		"ip": ip,
		"geo": geo,
		"shippingCountry": shipping_country,
		"signals": signals,
		"riskPoints": points,
		"riskScore": score,
	}


def _store(order, result, device):
	geo = result["geo"]
	location = ", ".join(v for v in (geo.get("city"), geo.get("region"), geo.get("country")) if v)
	previous = frappe.db.get_value(
		"Order Confirmation", order.name, ["custom_visitor_ip", "custom_risk_details"], as_dict=True
	)
	previous_fingerprint = (frappe.parse_json(previous.custom_risk_details or "{}") or {}).get("device", {}).get(
		"fingerprint"
	)

	details = {**result, "device": device, "checkedAt": str(now_datetime())}
	frappe.db.set_value(
		"Order Confirmation",
		order.name,
		{
			"custom_risk_score": result["riskScore"],
			"custom_risk_points": result["riskPoints"],
			"custom_visitor_ip": result["ip"],
			"custom_visitor_location": location[:140],
			"custom_visitor_network": (geo.get("org") or "")[:140],
			"custom_risk_checked_on": now_datetime(),
			"custom_risk_details": json.dumps(details, indent=2, default=str),
		},
		update_modified=False,
	)

	# history: one comment per new visitor (IP or device), same format the page used before
	if previous.custom_visitor_ip != result["ip"] or previous_fingerprint != device.get("fingerprint"):
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "Order Confirmation",
				"reference_name": order.name,
				"content": f"Fraud Data Logged:\n{frappe.as_json(details, indent=2)}",
			}
		).insert(ignore_permissions=True)


def get_shipping_country(order) -> str:
	for address in (order.get("shipping_address_name"), order.get("customer_address")):
		country = address and frappe.db.get_value("Address", address, "country")
		code = country and frappe.db.get_value("Country", country, "code")
		if code:
			return code.upper()
	return "IN"


# ------------------------------------------------------------------
# Lookups
# ------------------------------------------------------------------


def lookup_ip(ip) -> dict:
	geo = {"country": None, "countryCode": None, "city": None, "region": None, "timezone": None, "org": None}
	if not _is_public(ip):
		return geo

	city = _reader("city")
	if city:
		data = city.get(ip) or {}
		names = lambda d: (d or {}).get("names", {}).get("en")
		geo.update(
			country=names(data.get("country")),
			countryCode=((data.get("country") or {}).get("iso_code") or "").upper() or None,
			city=names(data.get("city")),
			region=names((data.get("subdivisions") or [{}])[0]),
			timezone=(data.get("location") or {}).get("time_zone"),
		)

	asn = _reader("asn")
	if asn:
		data = asn.get(ip) or {}
		org = data.get("autonomous_system_organization")
		number = data.get("autonomous_system_number")
		geo["org"] = f"AS{number} {org}" if number and org else org

	return geo


def in_list(kind, ip) -> bool:
	if not _is_public(ip) or ":" in ip:
		return False  # lists are IPv4 only
	ranges = _ranges(kind)
	if not ranges:
		return False
	value = int(ipaddress.IPv4Address(ip))
	i = bisect.bisect_right(ranges[0], value) - 1
	return i >= 0 and value <= ranges[1][i]


def _is_public(ip) -> bool:
	try:
		return ipaddress.ip_address(ip).is_global
	except (ValueError, TypeError):
		return False


def _path(kind):
	return os.path.join(get_data_dir(), FILES[kind])


def get_data_dir():
	return frappe.get_site_path("private", "order_confirmation_risk")


def _cached(kind, loader):
	path = _path(kind)
	try:
		mtime = os.path.getmtime(path)
	except OSError:
		return None
	cached = _cache.get(path)
	if not cached or cached[0] != mtime:
		try:
			cached = (mtime, loader(path))
		except Exception:
			frappe.log_error(title=f"Order Confirmation risk data unreadable: {kind}")
			return None
		_cache[path] = cached
	return cached[1]


def _reader(kind):
	import maxminddb

	return _cached(kind, maxminddb.open_database)


def _ranges(kind):
	def load(path):
		ranges = []
		with open(path) as f:
			for line in f:
				line = line.strip()
				if line and not line.startswith("#"):
					net = ipaddress.IPv4Network(line, strict=False)
					ranges.append((int(net.network_address), int(net.broadcast_address)))
		ranges.sort()
		return [r[0] for r in ranges], [r[1] for r in ranges]

	return _cached(kind, load)


# ------------------------------------------------------------------
# Data update (monthly scheduler job)
# ------------------------------------------------------------------


def update_risk_data():
	data_dir = get_data_dir()
	os.makedirs(data_dir, exist_ok=True)
	status = []

	for kind in ("city", "asn"):
		for month in (getdate(), add_months(getdate(), -1)):  # new month's file may not be out yet
			url = DBIP_URL.format(kind=kind, month=month.strftime("%Y-%m"))
			try:
				_download(url, _path(kind), gunzip=True)
				status.append(f"DB-IP {kind}: {month.strftime('%Y-%m')}")
				break
			except Exception as e:
				error = e
		else:
			status.append(f"DB-IP {kind}: FAILED ({error})")

	for kind in ("vpn", "datacenter"):
		try:
			_download(VPN_LIST_URL.format(kind=kind), _path(kind))
			status.append(f"X4BNet {kind} list: OK")
		except Exception as e:
			status.append(f"X4BNet {kind} list: FAILED ({e})")

	failed = any("FAILED" in s for s in status)
	if failed:
		frappe.log_error(title="Order Confirmation risk data update failed", message="\n".join(status))

	settings = frappe.get_single("Order Confirmation Settings")
	settings.db_set("risk_data_status", "\n".join(status))
	if not failed:
		settings.db_set("risk_data_updated_on", now_datetime())
	frappe.db.commit()
	return status


def _download(url, target, gunzip=False):
	with requests.get(url, stream=True, timeout=120) as response:
		response.raise_for_status()
		response.raw.decode_content = True  # GitHub serves the lists with gzip transfer encoding
		fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target))
		try:
			with os.fdopen(fd, "wb") as out:
				source = gzip.GzipFile(fileobj=response.raw) if gunzip else response.raw
				shutil.copyfileobj(source, out, 1024 * 1024)
			if os.path.getsize(tmp) < 1024:
				raise ValueError("downloaded file is too small")
			os.replace(tmp, target)  # atomic: readers never see a half-written file
		finally:
			if os.path.exists(tmp):
				os.remove(tmp)


# ------------------------------------------------------------------
# Custom fields on Order Confirmation
# ------------------------------------------------------------------


def setup_custom_fields():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	ro = {"read_only": 1, "no_copy": 1}
	create_custom_fields(
		{
			"Order Confirmation": [
				{
					"fieldname": "custom_risk_section",
					"fieldtype": "Section Break",
					"label": "Customer Risk Check",
					"insert_after": "verified",
					"collapsible": 1,
				},
				{
					"fieldname": "custom_risk_score",
					"fieldtype": "Select",
					"label": "Risk Score",
					"options": "\nLOW\nMEDIUM\nHIGH",
					"insert_after": "custom_risk_section",
					"in_standard_filter": 1,
					**ro,
				},
				{"fieldname": "custom_risk_points", "fieldtype": "Int", "label": "Risk Points", "insert_after": "custom_risk_score", **ro},
				{"fieldname": "custom_visitor_ip", "fieldtype": "Data", "label": "Visitor IP", "insert_after": "custom_risk_points", **ro},
				{"fieldname": "custom_visitor_location", "fieldtype": "Data", "label": "Visitor Location", "insert_after": "custom_visitor_ip", **ro},
				{"fieldname": "custom_risk_column", "fieldtype": "Column Break", "insert_after": "custom_visitor_location"},
				{"fieldname": "custom_visitor_network", "fieldtype": "Data", "label": "Visitor Network", "insert_after": "custom_risk_column", **ro},
				{"fieldname": "custom_risk_checked_on", "fieldtype": "Datetime", "label": "Risk Checked On", "insert_after": "custom_visitor_network", **ro},
				{"fieldname": "custom_risk_details", "fieldtype": "Code", "label": "Risk Details", "options": "JSON", "insert_after": "custom_risk_checked_on", **ro},
			]
		},
		update=True,
	)
	setup_status_options()


def setup_status_options():
	"""Status: add Pending (default) and fix the "Canclled" spelling.

	Property Setter rather than editing order_confirmation.json: the DocType in the
	database is newer than that JSON file, reloading it would undo those changes.
	"""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	for prop, value in (("options", "\nPending\nOrder Confirmed\nCancelled"), ("default", "Pending")):
		make_property_setter("Order Confirmation", "status", prop, value, "Text", validate_fields_for_doctype=False)
