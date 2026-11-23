"""Staff and what they were paid: the owner's replacement for the salary
spreadsheet. Not payroll: no tax or provident-fund maths, only who works
where, the agreed monthly salary and each payment, advance or deduction.

Business-wide (``org``) and visible only to owners/admins (``staff.manage``).
A staff member belongs to one branch; each payment keeps the branch it was
made at, so moving someone later doesn't rewrite a branch's past months.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from ims.api import MAX_PAISA


class StaffMember(models.Model):
    org = models.ForeignKey("tenancy.Organization", on_delete=models.PROTECT, related_name="staff")
    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT, related_name="staff")
    name = models.CharField(max_length=120)
    position = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    joined_on = models.DateField(null=True, blank=True)
    monthly_salary_paisa = models.BigIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(MAX_PAISA)])
    # Someone who left keeps their history; they just drop out of the monthly list.
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class StaffPayment(models.Model):
    SALARY, ADVANCE, BONUS, DEDUCTION, REPAYMENT = "salary", "advance", "bonus", "deduction", "repayment"
    KINDS = [
        (SALARY, "Salary"),
        (ADVANCE, "Advance / loan"),
        (BONUS, "Bonus / allowance"),
        (DEDUCTION, "Deduction"),
        (REPAYMENT, "Advance repaid"),
    ]
    # Money that leaves the shop. A deduction moves no money; a repayment comes back.
    PAID_OUT = (SALARY, ADVANCE, BONUS)
    CASH, BANK, WALLET, FROM_SALARY = "cash", "bank", "wallet", "salary"
    METHODS = [(CASH, "Cash"), (BANK, "Bank"), (WALLET, "Wallet"), (FROM_SALARY, "Cut from salary")]

    staff = models.ForeignKey(StaffMember, on_delete=models.PROTECT, related_name="payments")
    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT, related_name="staff_payments")
    kind = models.CharField(max_length=12, choices=KINDS)
    amount_paisa = models.BigIntegerField(validators=[MinValueValidator(1), MaxValueValidator(MAX_PAISA)])
    paid_on = models.DateField()
    # The BS month the entry counts toward, "2083-08" (salary for Kartik is often paid in Mangsir).
    month = models.CharField(max_length=7, db_index=True)
    method = models.CharField(max_length=8, choices=METHODS, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]
        indexes = [models.Index(fields=["staff", "month"])]
