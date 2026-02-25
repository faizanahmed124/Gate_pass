import frappe
from frappe.utils import get_datetime, add_days, time_diff_in_seconds
from frappe import _

def calculate_total_hours(doc, method=None):
    # This will run on validate/before_save - sirf total_hours calculate karega
    if not (doc.get("hours") and doc.get("to_time") and doc.get("date")):
        doc.total_hours = ""
        return

    # Calculate total hours for display
    date_str = str(doc.date)
    from_time_str = str(doc.hours)
    to_time_str = str(doc.to_time)
    
    from_dt = get_datetime(f"{date_str} {from_time_str}")
    to_dt = get_datetime(f"{date_str} {to_time_str}")

    if to_dt < from_dt:
        to_dt = add_days(to_dt, 1)

    diff_seconds = time_diff_in_seconds(to_dt, from_dt)
    total_minutes = int(diff_seconds / 60)

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours > 0:
        formatted_hours = f"{hours} hr {minutes} min" if minutes > 0 else f"{hours} hr"
        decimal_hours = round(hours + (minutes / 60), 2)
    else:
        formatted_hours = f"{minutes} min"
        decimal_hours = round(minutes / 60, 2)
    
    doc.total_hours = formatted_hours
    
    # Employee se allowed_hours fetch karo (sirf naye document ke liye)
    if doc.is_new() and doc.get("employee"):
        employee = frappe.get_doc("Employee", doc.employee)
        if employee.get("custom_allow_hours_for_leave") is not None:
            doc.allowed_hours = float(employee.get("custom_allow_hours_for_leave") or 0)


def on_submit(doc, method=None):
    """
    Ye function ON SUBMIT par run hoga
    """
    try:
        if not (doc.get("hours") and doc.get("to_time") and doc.get("date")):
            return

        # Calculate total hours in decimal
        date_str = str(doc.date)
        from_time_str = str(doc.hours)
        to_time_str = str(doc.to_time)
        
        from_dt = get_datetime(f"{date_str} {from_time_str}")
        to_dt = get_datetime(f"{date_str} {to_time_str}")

        if to_dt < from_dt:
            to_dt = add_days(to_dt, 1)

        diff_seconds = time_diff_in_seconds(to_dt, from_dt)
        total_minutes = int(diff_seconds / 60)

        hours = total_minutes // 60
        minutes = total_minutes % 60
        
        decimal_hours = round(hours + (minutes / 60), 2)
        gate_pass_overtime_decimal = decimal_hours

        # Get employee details
        if not doc.get("employee"):
            frappe.throw(_("Please select an employee first"))
        
        employee = frappe.get_doc("Employee", doc.employee)
        
        # Get employee's branch
        employee_branch = employee.get("branch") or ""
        
        # Define branches that use the leave hours deduction logic
        leave_hours_branches = ["MANAGERIAL STAFF", "PERMANENT STAFF WITHOUT OVERTIME"]
        
        # Check if employee belongs to specific branches for leave hours deduction
        if employee_branch.upper() in [branch.upper() for branch in leave_hours_branches]:
            # BRANCH-SPECIFIC LOGIC: Leave hours deduction with remaining check
            current_allowed_hours = float(employee.get("custom_allow_hours_for_leave") or 0)
            
            # Check if enough hours are available
            if current_allowed_hours < gate_pass_overtime_decimal:
                frappe.throw(
                    _("❌ Cannot Submit Gate Pass for {0} Branch!<br><br>"
                      "Employee has only {1} hrs remaining, but you're trying to deduct {2} hrs.<br>"
                      "Please adjust the time or contact HR.").format(
                        employee_branch,
                        current_allowed_hours,
                        gate_pass_overtime_decimal
                    ),
                    title=_("Insufficient Leave Hours")
                )
            
            # UPDATE EMPLOYEE LEAVE HOURS (minus operation)
            if gate_pass_overtime_decimal > 0:
                remaining_allowed_hours = round(current_allowed_hours - gate_pass_overtime_decimal, 2)
                employee.db_set("custom_allow_hours_for_leave", remaining_allowed_hours)
                
                frappe.msgprint(
                    _("✅ {0} Branch - Leave Hours Updated<br>"
                      "➖ Deducted: {1} hrs<br>"
                      "📉 Remaining: {2} hrs").format(
                        employee_branch,
                        gate_pass_overtime_decimal,
                        remaining_allowed_hours
                    ),
                    indicator="blue",
                    alert=True
                )
        
        # For ALL OTHER BRANCHES, use the overtime deduction logic
        # This runs regardless of branch, but for the specific branches above,
        # both leave hours AND overtime will be deducted
        
        # OVERTIME DEDUCTION LOGIC (for all branches if deduct_from_overtime is checked)
        if doc.get("deduct_from_overtime"):
            # Check for custom_allow_overtime field
            if not employee.get("custom_allow_overtime"):
                frappe.throw(
                    _("This Employee ({0}) is not applicable for overtime.").format(
                        employee.employee_name or employee.employee
                    ),
                    title=_("Overtime Not Allowed")
                )
            
            # Update attendance overtime
            if gate_pass_overtime_decimal > 0:
                # Find attendance for this employee on the given date
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
                    attendance_doc = frappe.get_doc("Attendance", attendance_name)
                    current_overtime = float(attendance_doc.get("custom_overtime") or 0)
                    
                    # MINUS operation (negative allowed for overtime)
                    remaining_overtime = round(current_overtime - gate_pass_overtime_decimal, 2)
                    
                    attendance_doc.db_set("custom_overtime", remaining_overtime)
                    
                    # Track reference
                    if attendance_doc.meta.has_field("custom_last_gate_pass_reference"):
                        attendance_doc.db_set("custom_last_gate_pass_reference", doc.name)
                    
                    branch_info = f" ({employee_branch})" if employee_branch else ""
                    status_icon = "🔴" if remaining_overtime < 0 else "🟢"
                    frappe.msgprint(
                        _("{0} Overtime Updated{1}<br>"
                          "Previous: {2} hrs → Remaining: {3} hrs").format(
                            status_icon,
                            branch_info,
                            current_overtime,
                            remaining_overtime
                        ),
                        indicator="blue" if remaining_overtime >= 0 else "orange",
                        alert=True
                    )
                else:
                    frappe.msgprint(
                        _("⚠️ No attendance record found for {0} on {1}").format(
                            employee.employee_name or doc.employee,
                            doc.date
                        ),
                        indicator="orange",
                        alert=True
                    )
        
        # Agar employee specific branch ka hai to overtime bhi hoga (upar ho chuka)
        # Agar employee specific branch ka nahi hai to sirf overtime hoga
        
    except frappe.ValidationError:
        raise
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Gate Pass On Submit Error")
        frappe.throw(_("Error processing submission: {0}").format(str(e)))