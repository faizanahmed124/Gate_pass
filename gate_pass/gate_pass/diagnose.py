"""Read-only: why is an employee's 'G.P' Additional Salary not on the salary slip?

    bench --site <site> execute gate_pass.gate_pass.diagnose.run --kwargs "{'employee': '1198'}"
"""

import frappe

from gate_pass.gate_pass.gate_pass_deduction import SALARY_COMPONENT as COMPONENT


def run(employee):
    from hrms.payroll.doctype.additional_salary.additional_salary import get_additional_salaries

    comp = frappe.db.get_value(
        "Salary Component", COMPONENT,
        ["type", "disabled", "do_not_include_in_total", "statistical_component", "depends_on_payment_days"],
        as_dict=True,
    )
    print(f"Component {COMPONENT}: {comp}\n")

    ads = frappe.get_all(
        "Additional Salary",
        filters={"employee": employee, "salary_component": COMPONENT, "docstatus": ["<", 2]},
        fields=["name", "amount", "payroll_date", "docstatus", "disabled", "type", "is_recurring", "overwrite_salary_structure_amount", "company"],
        order_by="payroll_date",
    )
    print("Additional Salary (not cancelled):")
    for a in ads:
        print("  ", a)

    print("\nSalary Slips:")
    for s in frappe.get_all(
        "Salary Slip", filters={"employee": employee, "docstatus": ["<", 2]},
        fields=["name", "start_date", "end_date", "docstatus", "company", "salary_structure"], order_by="start_date desc", limit=4,
    ):
        found = get_additional_salaries(employee, s.start_date, s.end_date, "deductions")
        rows = frappe.get_all("Salary Detail", filters={"parent": s.name, "parentfield": "deductions", "salary_component": COMPONENT}, fields=["amount", "additional_salary"])
        inside = [a.name for a in ads if a.payroll_date and s.start_date <= a.payroll_date <= s.end_date]
        print(f"  {s.name} {s.start_date}..{s.end_date} docstatus={s.docstatus} structure={s.salary_structure}")
        print(f"     Additional Salary dated inside this period: {inside or 'NONE'}")
        print(f"     HRMS would add: {[(f.name, f.amount) for f in found if f.component == COMPONENT] or 'nothing'}")
        print(f"     Gate pass row on slip: {rows or 'none'}")
