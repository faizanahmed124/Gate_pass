"""One-off: recompute Gate Pass deducted amounts for a period and (re)build the
monthly 'Deduction-Gate Pass' Additional Salary. Nothing is deleted: gate passes are
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
    """One-off: make sure the 'Deduction-Gate Pass' component exists (type Deduction) and give the
    already created gate pass Additional Salaries their missing type, so HRMS puts them on the slip.

        bench --site <site> execute gate_pass.gate_pass.backfill.fix_component --kwargs "{'dry_run': 1}"
    """
    from gate_pass.gate_pass.gate_pass_deduction import SALARY_COMPONENT, ensure_salary_component

    dry_run = cint(dry_run)
    print("Component exists:", bool(frappe.db.exists("Salary Component", SALARY_COMPONENT)))
    broken = frappe.db.sql(
        """select count(*) from `tabAdditional Salary`
        where salary_component = %s and docstatus = 1 and (type is null or type = '')""",
        SALARY_COMPONENT,
    )[0][0]
    print("Submitted Additional Salaries with empty type:", broken)
    if dry_run:
        print("DRY RUN: nothing changed")
        return
    ensure_salary_component()
    frappe.db.sql(
        """update `tabAdditional Salary` set type = 'Deduction'
        where salary_component = %s and (type is null or type = '')""",
        SALARY_COMPONENT,
    )
    frappe.db.commit()
    print("done: component ensured and", broken, "Additional Salaries set to Deduction")
