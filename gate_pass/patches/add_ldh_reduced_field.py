import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Gate Pass > "LDH Covered (hrs)": how many Less Duty Hours of that day's Attendance this
    gate pass covered. Filled by gate_pass_deduction.sync_day_ldh."""
    if not frappe.db.exists("DocType", "Gate Pass"):
        return

    field = {
        "fieldname": "ldh_reduced",
        "label": "LDH Covered (hrs)",
        "fieldtype": "Float",
        "precision": "2",
        "read_only": 1,
        "no_copy": 1,
        "description": "Less Duty Hours of that day's Attendance covered by this gate pass",
    }
    meta = frappe.get_meta("Gate Pass")
    if meta.has_field("remaining_hours"):
        field["insert_after"] = "remaining_hours"
    elif meta.has_field("allowed_hours"):
        field["insert_after"] = "allowed_hours"

    create_custom_fields({"Gate Pass": [field]}, update=True)
