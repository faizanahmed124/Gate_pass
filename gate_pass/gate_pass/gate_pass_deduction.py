import frappe
from frappe.utils import get_datetime, add_days, time_diff_in_seconds
from frappe import _


def calculate_total_hours(doc, method=None):
    """
    Runs on validate / before_save
    Calculates total_hours
    Also ensures allowed_hours never shows negative in Gate Pass
    """

    # ==============================
    # CALCULATE TOTAL HOURS
    # ==============================

    if doc.get("hours") and doc.get("to_time") and doc.get("date"):

        date_str = str(doc.date)
        from_time_str = str(doc.hours)
        to_time_str = str(doc.to_time)

        from_dt = get_datetime(f"{date_str} {from_time_str}")
        to_dt = get_datetime(f"{date_str} {to_time_str}")

        if to_dt < from_dt:
            to_dt = add_days(to_dt, 1)

        diff_seconds = time_diff_in_seconds(to_dt, from_dt)
        total_minutes = int(diff_seconds / 60)

        hrs = total_minutes // 60
        mins = total_minutes % 60

        if hrs > 0:
            doc.total_hours = f"{hrs} hr {mins} min" if mins else f"{hrs} hr"
        else:
            doc.total_hours = f"{mins} min"

    else:
        doc.total_hours = ""

    # ==========================================
    # ALWAYS FETCH ALLOWED HOURS (NO NEGATIVE)
    # ==========================================

    if doc.get("employee"):
        employee_allowed = frappe.db.get_value(
            "Employee",
            doc.employee,
            "custom_allow_hours_for_leave"
        )

        employee_allowed = float(employee_allowed or 0)

        # 🚫 Never show negative in Gate Pass
        doc.allowed_hours = employee_allowed if employee_allowed > 0 else 0


def on_submit(doc, method=None):
    """
    Runs on Submit
    Deducts Leave / Overtime
    """

    try:

        if not (doc.get("hours") and doc.get("to_time") and doc.get("date")):
            return

        # ==============================
        # CALCULATE DECIMAL HOURS
        # ==============================

        date_str = str(doc.date)
        from_time_str = str(doc.hours)
        to_time_str = str(doc.to_time)

        from_dt = get_datetime(f"{date_str} {from_time_str}")
        to_dt = get_datetime(f"{date_str} {to_time_str}")

        if to_dt < from_dt:
            to_dt = add_days(to_dt, 1)

        diff_seconds = time_diff_in_seconds(to_dt, from_dt)
        total_minutes = int(diff_seconds / 60)

        hrs = total_minutes // 60
        mins = total_minutes % 60

        decimal_hours = round(hrs + (mins / 60), 2)

        # ==============================
        # GET EMPLOYEE
        # ==============================

        if not doc.get("employee"):
            frappe.throw(_("Please select an employee first"))

        employee = frappe.get_doc("Employee", doc.employee)
        employee_branch = employee.get("branch") or ""

        leave_hours_branches = [
            "MANAGERIAL STAFF",
            "PERMANENT STAFF WITHOUT OVERTIME"
        ]

        # ======================================
        # LEAVE HOURS DEDUCTION (Minus Allowed)
        # ======================================

        if employee_branch.upper() in [b.upper() for b in leave_hours_branches]:

            current_allowed = float(
                employee.get("custom_allow_hours_for_leave") or 0
            )

            if decimal_hours > 0:

                if decimal_hours > current_allowed:
                    frappe.msgprint(
                        _("⚠️ Changing hours into minus<br>"
                          "Allowed: {0} hrs<br>"
                          "Gate Pass: {1} hrs").format(
                            current_allowed,
                            decimal_hours
                        ),
                        indicator="orange",
                        alert=True
                    )

                remaining = round(current_allowed - decimal_hours, 2)

                # Update Employee (minus allowed)
                employee.db_set(
                    "custom_allow_hours_for_leave",
                    remaining
                )

                frappe.msgprint(
                    _("✅ Leave Hours Updated<br>"
                      "Remaining: {0} hrs").format(remaining),
                    indicator="blue",
                    alert=True
                )

        # ======================================
        # OVERTIME DEDUCTION
        # ======================================

        if doc.get("deduct_from_overtime"):

            if not employee.get("custom_allow_overtime"):
                frappe.throw(
                    _("This Employee ({0}) is not applicable for overtime.").format(
                        employee.employee_name or employee.employee
                    ),
                    title=_("Overtime Not Allowed")
                )

            attendance_name = frappe.get_value(
                "Attendance",
                {
                    "employee": doc.employee,
                    "attendance_date": doc.date,
                    "docstatus": 1
                },
                "name"
            )

            if attendance_name:

                attendance_doc = frappe.get_doc(
                    "Attendance",
                    attendance_name
                )

                current_ot = float(
                    attendance_doc.get("custom_overtime") or 0
                )

                remaining_ot = round(current_ot - decimal_hours, 2)

                attendance_doc.db_set(
                    "custom_overtime",
                    remaining_ot
                )

                frappe.msgprint(
                    _("Overtime Updated<br>"
                      "Remaining: {0} hrs").format(remaining_ot),
                    indicator="blue",
                    alert=True
                )

            else:
                frappe.msgprint(
                    _("⚠️ No attendance found for this date"),
                    indicator="orange",
                    alert=True
                )

    except frappe.ValidationError:
        raise

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Gate Pass On Submit Error"
        )
        frappe.throw(
            _("Error processing submission. Contact Administrator.")
        )