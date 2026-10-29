import frappe
from frappe.utils import flt, formatdate, get_first_day, get_last_day, get_datetime, add_days, time_diff_in_seconds
from frappe import _


DEDUCTION_FIELDS = [
    "deduct_from_salary",
    "deduct_from_overtime",
    "deduct_from_duty_hours",
]


def validate_single_deduction(doc):
    """Only one 'Deduct From' option may be selected at a time."""
    selected = [
        _(doc.meta.get_label(f)).strip() for f in DEDUCTION_FIELDS if doc.get(f)
    ]
    if len(selected) > 1:
        frappe.throw(
            _("Only one deduction option can be selected at a time. Selected: {0}").format(
                ", ".join(selected)
            ),
            title=_("Multiple Deductions Selected"),
        )


WORKING_DAYS_PER_MONTH = 30
WORKING_HOURS_PER_DAY = 8


def get_hourly_rate(employee):
    """Employee CTC (gross pay) / 30 days / 8 hours."""
    ctc = frappe.db.get_value("Employee", employee, "ctc")
    return flt(ctc) / WORKING_DAYS_PER_MONTH / WORKING_HOURS_PER_DAY


def calculate_deduction_amount(doc, total_minutes):
    """Amount for the gate pass period; only when deducting from salary."""
    if not (doc.get("deduct_from_salary") and doc.get("employee") and total_minutes):
        return 0

    rate = get_hourly_rate(doc.employee)
    if not rate:
        frappe.msgprint(
            _("CTC is not set for {0}; deduction amount is 0").format(
                doc.employee
            ),
            indicator="orange",
            alert=True,
        )
    return flt(rate * total_minutes / 60, 0)


def calculate_total_hours(doc, method=None):
    """
    Runs on validate / before_save
    Calculates total_hours
    Also ensures allowed_hours never shows negative in Gate Pass
    """

    validate_single_deduction(doc)

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

        doc.calculate_total_hours = round(total_minutes / 60, 3)
        doc.deducted_amount = calculate_deduction_amount(doc, total_minutes)

        if hrs > 0:
            doc.total_hours = f"{hrs} hr {mins} min" if mins else f"{hrs} hr"
        else:
            doc.total_hours = f"{mins} min"

    else:
        doc.total_hours = ""
        doc.calculate_total_hours = 0
        doc.deducted_amount = 0

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


SALARY_COMPONENT = "Deduction-Gate Pass"


def sync_additional_salary(doc, method=None):
    """Keep ONE Additional Salary per employee per month for all gate passes.

    HRMS adds one salary slip row per Additional Salary, so the month's
    submitted gate pass amounts are summed into a single Additional Salary
    (payroll date = month end), recreated whenever a gate pass is submitted
    or cancelled.
    """
    if not (doc.get("employee") and doc.get("date")):
        return

    month_start, month_end = get_first_day(doc.date), get_last_day(doc.date)

    total = flt(
        frappe.db.sql(
            """
            select sum(cast(nullif(deducted_amount, '') as decimal(18, 2))) from `tabGate Pass`
            where employee = %s and docstatus = 1 and deduct_from_salary = 1
                and date between %s and %s
            """,
            (doc.employee, month_start, month_end),
        )[0][0]
    )

    existing = frappe.get_all(
        "Additional Salary",
        filters={
            "employee": doc.employee,
            "salary_component": SALARY_COMPONENT,
            "docstatus": 1,
            "payroll_date": ["between", [month_start, month_end]],
        },
        fields=["name", "amount", "payroll_date"],
    )

    if total > 0 and len(existing) == 1 and (
        flt(existing[0].amount) == total and existing[0].payroll_date == month_end
    ):
        return

    for row in existing:
        frappe.get_doc("Additional Salary", row.name).cancel()

    if total <= 0:
        return

    additional_salary = frappe.get_doc(
        {
            "doctype": "Additional Salary",
            "employee": doc.employee,
            "company": frappe.db.get_value("Employee", doc.employee, "company"),
            "salary_component": SALARY_COMPONENT,
            "amount": total,
            "payroll_date": month_end,
            "overwrite_salary_structure_amount": 0,
        }
    )
    additional_salary.insert()
    additional_salary.submit()

    frappe.msgprint(
        _("Additional Salary {0}: {1} gate pass deduction for {2}").format(
            additional_salary.name, total, formatdate(month_end, "MMM yyyy")
        ),
        indicator="green",
        alert=True,
    )


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