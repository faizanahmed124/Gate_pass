"""One-off: recompute Gate Pass deducted amounts for a period and (re)build the
monthly 'G.P' (Deduction-Gate Pass) Additional Salary. Nothing is deleted: gate passes are
only updated in place, and an existing Additional Salary is cancelled (not deleted)
and replaced by one with the new total.

    bench --site <site> execute gate_pass.gate_pass.backfill.run \
        --kwargs "{'from_date': '2026-10-01', 'to_date': '2026-10-31', 'dry_run': 1}"

Run with dry_run 1 first (default), check the output, then dry_run 0.
"""

import frappe
from frappe.utils import cint, cstr, flt

from gate_pass.gate_pass.gate_pass_deduction import (
    calculate_deduction_amount,
    get_total_minutes,
    sync_additional_salary,
)


def run(from_date="2026-10-01", to_date="2026-10-31", dry_run=1):
    dry_run = cint(dry_run)
    passes = frappe.get_all(
        "Gate Pass",
        filters={
            "docstatus": 1,
            "deduct_from_salary": 1,
            "date": ["between", [from_date, to_date]],
        },
        pluck="name",
        order_by="employee, date",
    )
    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'}: {len(passes)} submitted gate passes\n")

    employees = {}
    for name in passes:
        doc = frappe.get_doc("Gate Pass", name)
        amount = calculate_deduction_amount(doc, get_total_minutes(doc))
        print(f"{name:20} {doc.employee:8} {doc.date}  old={cstr(doc.get('deducted_amount')):>10}  new={amount:>10}")
        if not dry_run:
            frappe.db.set_value(
                "Gate Pass", name, "deducted_amount", cstr(int(amount)), update_modified=False
            )
        employees.setdefault(doc.employee, doc)

    print()
    for employee, doc in employees.items():
        if dry_run:
            print(f"would rebuild Additional Salary for {employee}")
            continue
        try:
            sync_additional_salary(doc)
            frappe.db.commit()
            print(f"OK    {employee}")
        except Exception as e:
            frappe.db.rollback()
            print(f"FAILED {employee}: {e}")

    if not dry_run:
        frappe.db.commit()


def _stale_rows():
    """Deduction rows of DRAFT slips still carrying the old component name. HRMS would keep them
    and add a second G.P row (double deduction), so they are renamed to G.P before recalculating."""
    from gate_pass.gate_pass.gate_pass_deduction import LEGACY_COMPONENT

    return frappe.db.sql_list(
        """select sd.name from `tabSalary Detail` sd join `tabSalary Slip` ss on ss.name = sd.parent
        where ss.docstatus = 0 and sd.parentfield = 'deductions' and sd.salary_component = %s""",
        LEGACY_COMPONENT,
    )


def _rename_stale_rows():
    from gate_pass.gate_pass.gate_pass_deduction import LEGACY_COMPONENT, SALARY_COMPONENT

    comp = frappe.db.get_value(
        "Salary Component", SALARY_COMPONENT,
        ["salary_component_abbr", "depends_on_payment_days", "do_not_include_in_total"], as_dict=True,
    ) or {}
    rows = _stale_rows()
    for name in rows:
        # take the row's flags from the real component too, so the amount is not prorated by payment days
        frappe.db.set_value(
            "Salary Detail", name,
            {
                "salary_component": SALARY_COMPONENT,
                "abbr": comp.get("salary_component_abbr"),
                "depends_on_payment_days": comp.get("depends_on_payment_days") or 0,
                "do_not_include_in_total": comp.get("do_not_include_in_total") or 0,
            },
            update_modified=False,
        )
    return len(rows)


def fix_component(dry_run=1):
    """One-off: gate pass Additional Salaries were created under the component name
    'Deduction-Gate Pass' (which does not exist on the live site) with an empty type, so HRMS
    skipped them on the salary slip. Move them to the real component 'G.P' (Deduction).

        bench --site <site> execute gate_pass.gate_pass.backfill.fix_component --kwargs "{'dry_run': 1}"
    """
    from gate_pass.gate_pass.gate_pass_deduction import LEGACY_COMPONENT, SALARY_COMPONENT, ensure_salary_component

    dry_run = cint(dry_run)
    print(f"Component {SALARY_COMPONENT} exists:", bool(frappe.db.exists("Salary Component", SALARY_COMPONENT)))
    old = frappe.db.sql(
        "select count(*) from `tabAdditional Salary` where salary_component = %s", LEGACY_COMPONENT
    )[0][0]
    empty = frappe.db.sql(
        """select count(*) from `tabAdditional Salary`
        where salary_component = %s and (type is null or type = '')""",
        SALARY_COMPONENT,
    )[0][0]
    print(f"Additional Salaries under '{LEGACY_COMPONENT}': {old} | under '{SALARY_COMPONENT}' with empty type: {empty}")
    print(f"Draft slip rows still named '{LEGACY_COMPONENT}': {len(_stale_rows())}")
    if dry_run:
        print("DRY RUN: nothing changed")
        return
    ensure_salary_component()
    frappe.db.sql(
        "update `tabAdditional Salary` set salary_component = %s, type = 'Deduction' where salary_component = %s",
        (SALARY_COMPONENT, LEGACY_COMPONENT),
    )
    frappe.db.sql(
        """update `tabAdditional Salary` set type = 'Deduction'
        where salary_component = %s and (type is null or type = '')""",
        SALARY_COMPONENT,
    )
    renamed = _rename_stale_rows()
    frappe.db.commit()
    print(f"done ({renamed} draft slip rows renamed to {SALARY_COMPONENT})")


def refresh_draft_slips(from_date, to_date, dry_run=1):
    """One-off: recalculate the DRAFT salary slips that should carry a gate pass (G.P)
    Additional Salary but were created before it was fixed (slips only pick it up when recalculated).

        bench --site <site> execute gate_pass.gate_pass.backfill.refresh_draft_slips \\
            --kwargs "{'from_date': '2026-10-16', 'to_date': '2026-10-31', 'dry_run': 1}"
    """
    from overtime_in_salary.overtime_in_salary.utils.salary_triggers import auto_recalculate_draft_slips

    dry_run = cint(dry_run)
    from gate_pass.gate_pass.gate_pass_deduction import SALARY_COMPONENT

    employees = frappe.db.sql_list(
        """select distinct employee from `tabAdditional Salary`
        where salary_component = %s and docstatus = 1
        and payroll_date between %s and %s""",
        (SALARY_COMPONENT, from_date, to_date),
    )
    slips = frappe.get_all(
        "Salary Slip",
        filters={"employee": ["in", employees or [""]], "docstatus": 0,
                 "start_date": ["<=", to_date], "end_date": [">=", from_date]},
        pluck="name",
    )
    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'}: {len(employees)} employees with a gate pass deduction, {len(slips)} draft slips in {from_date}..{to_date}")
    print(f"Draft slip rows still named with the old component name: {len(_stale_rows())}")
    if dry_run:
        return
    _rename_stale_rows()
    frappe.db.commit()
    for employee in employees:
        auto_recalculate_draft_slips(employee, from_date, to_date)
    print("done")


def give_monthly_hours(hours=8, as_on=None, dry_run=1, branches=None):
    """Monthly allowed (leave) hours: set `Allow Hours For Leave` on the Employee to `hours` for
    Active PERMANENT employees who are NOT allowed overtime and have completed more than 3 months
    since their date of joining. Everyone else is left alone. A negative balance is reset too.

        bench --site <site> execute gate_pass.gate_pass.backfill.give_monthly_hours --kwargs "{'hours': 8, 'dry_run': 1}"

    `branches` (optional list) limits it to those branches, e.g. {'branches': ['MANAGERIAL STAFF', 'PERMANENT STAFF WITHOUT OVERTIME']}.
    """
    from frappe.utils import add_months, flt, getdate, nowdate

    dry_run = cint(dry_run)
    hours = flt(hours)
    as_on = getdate(as_on or nowdate())
    cutoff = add_months(as_on, -3)

    filters = {
        "status": "Active",
        "employment_type": "Permanent",
        "custom_allow_overtime": 0,
        "date_of_joining": ["<", cutoff],
    }
    if branches:
        filters["branch"] = ["in", branches]
    emps = frappe.get_all(
        "Employee",
        filters=filters,
        fields=["name", "employee_name", "branch", "custom_allow_hours_for_leave"],
        order_by="branch, name",
    )
    value = "%g" % hours
    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'}: {len(emps)} Active Permanent employees without overtime, joined before {cutoff} -> {value} hrs\n")

    by_branch = {}
    changed = negative = 0
    for e in emps:
        current = flt(e.custom_allow_hours_for_leave)
        row = by_branch.setdefault(e.branch or "(no branch)", [0, 0])
        row[0] += 1
        if (e.custom_allow_hours_for_leave or "") != value:
            row[1] += 1
            changed += 1
            if current < 0:
                negative += 1
        if not dry_run:
            frappe.db.set_value("Employee", e.name, "custom_allow_hours_for_leave", value, update_modified=False)

    print(f"{'Branch':40} {'employees':>9} {'will change':>12}")
    for branch, (n, c) in by_branch.items():
        print(f"{branch:40} {n:>9} {c:>12}")
    print(f"\n{changed} employees change value ({negative} of them have a negative balance that gets reset)")
    if not dry_run:
        frappe.db.commit()
        print("done")
    else:
        print("DRY RUN: nothing changed")


def deduct_allowed_hours(from_date="2026-11-01", to_date="2026-11-12", dry_run=1, only_overtime=1):
    """One-off: take the hours of the SUBMITTED gate passes in the period off the employee's
    `Allow Hours For Leave` (what on_submit does for gate passes made after the rule), for
    PERMANENT employees who are allowed overtime. The same rule as on_submit applies:

      * no "Deduct From" option ticked on the gate pass
      * more than 3 months since the date of joining on the gate pass date
      * gate passes already carrying the "Allowed hours deducted" comment are skipped,
        and every gate pass handled here gets that comment, so running it twice never
        deducts twice.

    Each handled gate pass also gets its "Remaining Allowed Hours" (running balance).

        bench --site <site> execute gate_pass.gate_pass.backfill.deduct_allowed_hours \\
            --kwargs "{'from_date': '2026-11-01', 'to_date': '2026-11-12', 'dry_run': 1}"

    Run with dry_run 1 first and check the output, then dry_run 0.
    `only_overtime` 0 = every Permanent employee, not only those allowed overtime.
    """
    from frappe.utils import flt

    from gate_pass.gate_pass.gate_pass_deduction import (
        DEDUCTION_FIELDS,
        deducts_allowed_hours,
        get_decimal_hours,
    )

    dry_run = cint(dry_run)
    only_overtime = cint(only_overtime)

    passes = frappe.get_all(
        "Gate Pass",
        filters={"docstatus": 1, "date": ["between", [from_date, to_date]]},
        fields=["name", "employee", "date", "hours", "to_time"] + DEDUCTION_FIELDS,
        order_by="employee, date, creation",
    )

    emp_cache = {}

    def employee_info(employee):
        if employee not in emp_cache:
            emp_cache[employee] = frappe.db.get_value(
                "Employee",
                employee,
                ["employee_name", "employment_type", "date_of_joining",
                 "custom_allow_overtime", "custom_allow_hours_for_leave"],
                as_dict=True,
            ) or {}
        return emp_cache[employee]

    picked, skipped = {}, []
    for p in passes:
        emp = employee_info(p.employee)
        hours = get_decimal_hours(p)

        reason = None
        if not emp:
            reason = "employee not found"
        elif (emp.get("employment_type") or "").strip().lower() != "permanent":
            reason = "not permanent"
        elif only_overtime and not emp.get("custom_allow_overtime"):
            reason = "overtime not allowed"
        elif any(p.get(f) for f in DEDUCTION_FIELDS):
            reason = "a 'Deduct From' option is ticked"
        elif not deducts_allowed_hours(p, emp):
            reason = "3 months of service not completed"
        elif hours <= 0:
            reason = "no hours"
        elif frappe.db.exists(
            "Comment",
            {"reference_doctype": "Gate Pass", "reference_name": p.name, "comment_type": "Info",
             "content": ["like", "Allowed hours deducted%"]},
        ):
            reason = "already deducted"

        if reason:
            # only report the ones the OT / permanent rule is about, not every other employee
            if emp and (emp.get("employment_type") or "").strip().lower() == "permanent" and (
                emp.get("custom_allow_overtime") or not only_overtime
            ):
                skipped.append((p.name, p.employee, emp.get("employee_name"), p.date, hours, reason))
            continue
        picked.setdefault(p.employee, []).append((p, hours))

    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'}: gate passes {from_date} .. {to_date}, "
          f"{'permanent employees with overtime' if only_overtime else 'all permanent employees'}\n")

    print(f"{'Emp':8} {'Name':28} {'Passes':>6} {'Hours':>7} {'Allowed':>8} {'Remaining':>9}")
    total_hours = total_passes = 0
    for employee, rows in picked.items():
        emp = employee_info(employee)
        before = flt(emp.get("custom_allow_hours_for_leave"))
        hours = round(sum(h for _p, h in rows), 2)
        after = round(before - hours, 2)
        total_hours += hours
        total_passes += len(rows)
        flag = "  <- goes into minus" if after < 0 else ""
        print(f"{employee:8} {(emp.get('employee_name') or '')[:28]:28} {len(rows):>6} {hours:>7} {before:>8} {after:>9}{flag}")

        if dry_run:
            continue
        try:
            running = before
            for p, h in rows:
                running = round(running - h, 2)
                if frappe.db.has_column("Gate Pass", "remaining_hours"):
                    frappe.db.set_value("Gate Pass", p.name, "remaining_hours", running, update_modified=False)
                frappe.get_doc("Gate Pass", p.name).add_comment("Info", f"Allowed hours deducted: {h} hrs")
            frappe.db.set_value("Employee", employee, "custom_allow_hours_for_leave", after, update_modified=False)
            frappe.db.commit()
        except Exception as e:
            frappe.db.rollback()
            print(f"   FAILED {employee}: {e}")

    print(f"\n{len(picked)} employees, {total_passes} gate passes, {round(total_hours, 2)} hours "
          f"{'would be' if dry_run else 'were'} deducted")

    if skipped:
        print("\nNot deducted:")
        for name, employee, ename, date, hours, reason in skipped:
            print(f"  {name:22} {employee:8} {(ename or '')[:24]:24} {date}  {hours:>5} hrs  -> {reason}")

    print("\nDRY RUN: nothing changed" if dry_run else "\ndone")
