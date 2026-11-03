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
    frappe.db.commit()
    print("done")


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
    if dry_run:
        return
    for employee in employees:
        auto_recalculate_draft_slips(employee, from_date, to_date)
    print("done")
