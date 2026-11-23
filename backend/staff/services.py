"""Monthly figures per staff member, BS months, and the staff-list import."""
import re
from datetime import date

import nepali_datetime as nd
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from admin_extras.imports import _file_error, _rows, rupees_to_paisa
from compliance import bs
from tenancy.models import Branch

from .models import StaffMember, StaffPayment

MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def month_of(d: date) -> str:
    b = bs.to_bs(d)
    return f"{b.year:04d}-{b.month:02d}"


def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{bs.month_name(int(m))} {y}"


def parse_month(raw) -> str | None:
    """'2083-08' if valid (and convertible), else None."""
    m = MONTH_RE.match(str(raw or "").strip())
    if not m:
        return None
    try:
        nd.date(int(m.group(1)), int(m.group(2)), 1)
    except (ValueError, KeyError, IndexError):
        return None
    return m.group(0)


def recent_months(count=13, today=None):
    """This BS month and the ones before it, newest first."""
    b = bs.to_bs(today or timezone.localdate())
    y, m, out = b.year, b.month, []
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def month_summary(org, month, branch=None):
    """One row per staff member: the agreed salary, what this month's entries
    add up to, what is still to pay, and the advance still owed (all time).

    "Still to pay" = salary − deductions − advance cut from salary − salary paid
    (never below zero). Bonuses are extra and don't count toward it."""
    first, last = bs.bs_month_bounds(*map(int, month.split("-")))
    staff = StaffMember.objects.filter(org=org).select_related("branch")
    if branch is not None:
        staff = staff.filter(branch=branch)
    ids = [s.id for s in staff]

    def sums(qs):
        return {r["staff_id"]: r["total"] for r in qs.values("staff_id").annotate(total=Sum("amount_paisa"))}

    this = StaffPayment.objects.filter(staff_id__in=ids, month=month)
    paid = sums(this.filter(kind=StaffPayment.SALARY))
    bonus = sums(this.filter(kind=StaffPayment.BONUS))
    advance = sums(this.filter(kind=StaffPayment.ADVANCE))
    deducted = sums(this.filter(Q(kind=StaffPayment.DEDUCTION)
                                | Q(kind=StaffPayment.REPAYMENT, method=StaffPayment.FROM_SALARY)))
    lent = sums(StaffPayment.objects.filter(staff_id__in=ids, kind=StaffPayment.ADVANCE))
    repaid = sums(StaffPayment.objects.filter(staff_id__in=ids, kind=StaffPayment.REPAYMENT))
    touched = set(this.values_list("staff_id", flat=True))

    rows = []
    for s in staff:
        # Shown for the month if they were working then, or anything was recorded for them.
        working = s.is_active and (s.joined_on is None or s.joined_on <= last)
        if not working and s.id not in touched:
            continue
        salary = s.monthly_salary_paisa if working else 0
        due = max(0, salary - deducted.get(s.id, 0) - paid.get(s.id, 0))
        rows.append({
            "id": s.id, "name": s.name, "position": s.position, "phone": s.phone,
            "branch": s.branch_id, "branch_code": s.branch.code, "branch_name": s.branch.name,
            "is_active": s.is_active, "monthly_salary_paisa": s.monthly_salary_paisa,
            "salary_paid_paisa": paid.get(s.id, 0), "bonus_paisa": bonus.get(s.id, 0),
            "advance_paisa": advance.get(s.id, 0), "deductions_paisa": deducted.get(s.id, 0),
            "due_paisa": due, "advance_owed_paisa": lent.get(s.id, 0) - repaid.get(s.id, 0),
        })
    keys = ("monthly_salary_paisa", "salary_paid_paisa", "bonus_paisa", "advance_paisa", "deductions_paisa",
            "due_paisa", "advance_owed_paisa")
    totals = {k: sum(r[k] for r in rows) for k in keys}
    totals["paid_out_paisa"] = totals["salary_paid_paisa"] + totals["bonus_paisa"] + totals["advance_paisa"]
    return {
        "month": month, "month_label": month_label(month), "from": first.isoformat(), "to": last.isoformat(),
        "rows": rows, "totals": totals, "not_paid": sum(1 for r in rows if r["due_paisa"] > 0),
    }


def payments_report(org, month, branch=None):
    """The month's entries in the shape compliance.exports renders (Excel/CSV/PDF)."""
    qs = StaffPayment.objects.filter(staff__org=org, month=month).select_related("staff", "branch")
    if branch is not None:
        qs = qs.filter(branch=branch)
    kinds, methods = dict(StaffPayment.KINDS), dict(StaffPayment.METHODS)
    payments = list(qs.order_by("paid_on", "id"))
    rows = [{
        "date_bs": bs.bs_str(p.paid_on), "date": p.paid_on.isoformat(), "name": p.staff.name, "branch": p.branch.code,
        "kind": kinds[p.kind], "method": methods.get(p.method, ""), "amount": p.amount_paisa, "note": p.note,
    } for p in payments]
    out = sum(p.amount_paisa for p in payments if p.kind in StaffPayment.PAID_OUT)
    return {
        "title": f"Staff payments · {month_label(month)}",
        "subtitle": f"{org.name}{' · ' + branch.name if branch else ''}",
        "columns": [("date_bs", "Date (BS)", "text"), ("date", "Date (AD)", "text"), ("name", "Staff", "text"),
                    ("branch", "Branch", "text"), ("kind", "Type", "text"), ("method", "Paid by", "text"),
                    ("amount", "Amount (Rs)", "money"), ("note", "Note", "text")],
        "rows": rows,
        "totals": {"amount": out},
        "notes": ["Total = money paid out (salary, bonus, advances). Deductions and repayments are listed but not added."],
    }




def _date(raw, field):
    text = str(raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field}: {raw!r} is not a date (use YYYY-MM-DD, in AD)")


def import_staff(org, file_bytes, dry_run=True):
    """Staff list from CSV/Excel. A row whose name already exists at that
    branch updates that person, so the same file twice adds nobody twice."""
    try:
        rows = list(_rows(file_bytes))
    except ValueError as e:
        return _file_error(e)
    branches = {b.code.lower(): b for b in Branch.objects.filter(org=org)}
    existing = {(s.branch_id, s.name.lower()): s for s in StaffMember.objects.filter(org=org)}
    errors, plan, seen = [], [], set()
    for n, r in enumerate(rows, start=2):
        try:
            name = (r.get("name") or "").strip()[:120]
            if not name:
                raise ValueError("name is required")
            code = (r.get("branch") or "").strip().lower()
            branch = branches.get(code)
            if branch is None:
                raise ValueError(f"branch {r.get('branch')!r} isn't one of this business's branch codes"
                                 f" ({', '.join(sorted(b.code for b in branches.values()))})")
            if (branch.id, name.lower()) in seen:
                raise ValueError(f"{name} is listed twice for {branch.code}")
            seen.add((branch.id, name.lower()))
            salary = rupees_to_paisa(r.get("salary") or "0", "salary")
            if salary > 10 ** 13:
                raise ValueError("salary is too large")
            fields = {"position": (r.get("position") or "").strip()[:80], "phone": (r.get("phone") or "").strip()[:40],
                      "joined_on": _date(r.get("joined"), "joined"), "monthly_salary_paisa": salary}
        except ValueError as e:
            errors.append({"row": n, "error": str(e)})
            continue
        current = existing.get((branch.id, name.lower()))
        plan.append((n, current, branch, name, fields))
    result = {
        "ok": not errors, "dry_run": dry_run, "errors": errors,
        "creates": sum(1 for p in plan if p[1] is None), "updates": sum(1 for p in plan if p[1] is not None),
        "rows": [{"row": n, "name": name, "branch": branch.code, "salary_paisa": f["monthly_salary_paisa"],
                  "action": "update" if cur else "create"} for n, cur, branch, name, f in plan],
    }
    if dry_run or errors:
        return result
    with transaction.atomic():
        _apply(org, plan)
    return result


def _apply(org, plan):
    for _, current, branch, name, fields in plan:
        if current is None:
            StaffMember.objects.create(org=org, branch=branch, name=name, **fields)
        else:
            for k, v in fields.items():
                setattr(current, k, v)
            current.save()
