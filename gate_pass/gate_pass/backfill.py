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
