import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Gate Pass > "Remaining Allowed Hours", shown right below "Allowed Hours".
    Filled on validate (Allowed Hours - this gate pass) and by backfill.deduct_allowed_hours."""
    if not frappe.db.exists("DocType", "Gate Pass"):
        return

    field = {
        "fieldname": "remaining_hours",
        "label": "Remaining Allowed Hours",
        "fieldtype": "Float",
        "precision": "2",
        "read_only": 1,
        "no_copy": 1,
        "description": "Allow Hours For Leave left after this gate pass",
    }
    if frappe.get_meta("Gate Pass").has_field("allowed_hours"):
        field["insert_after"] = "allowed_hours"

    create_custom_fields({"Gate Pass": [field]}, update=True)
