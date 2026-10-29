const DEDUCT_FIELDS = [
    "deduct_from_salary",
    "deduct_from_overtime",
    "deduct_from_duty_hours",
];

// Only one "Deduct From" checkbox may be ticked at a time.
const exclusive = (field) => (frm) => {
    if (!frm.doc[field]) return;
    DEDUCT_FIELDS.filter((f) => f !== field && frm.doc[f]).forEach((f) =>
        frm.set_value(f, 0)
    );
};

frappe.ui.form.on("Gate Pass", {
    deduct_from_salary: exclusive("deduct_from_salary"),
    deduct_from_overtime: exclusive("deduct_from_overtime"),
    deduct_from_duty_hours: exclusive("deduct_from_duty_hours"),
});
