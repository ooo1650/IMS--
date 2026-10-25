"""Bikram Sambat (BS) dates and the Nepali fiscal year (payment.md Step 2).

The fiscal year runs Shrawan 1 – Asar end. Conversion uses the
``nepali-datetime`` calendar tables; checked 2026-10-25 = Kartik 8, 2083
against published patros. Keep the package updated each year — BS month
lengths for future years are fixed by the Nepal Panchanga Nirnayak Samiti.
"""
from datetime import date, datetime, time

import nepali_datetime as nd
from django.utils import timezone

SHRAWAN = 4
MONTHS = ["Baisakh", "Jestha", "Asar", "Shrawan", "Bhadra", "Asoj", "Kartik", "Mangsir", "Poush", "Magh",
          "Falgun", "Chaitra"]


def to_bs(d: date) -> nd.date:
    return nd.date.from_datetime_date(d)


def bs_str(d: date | None, sep=".") -> str:
    """2026-10-25 -> '2083.07.08' (the CBMS format)."""
    if d is None:
        return ""
    b = to_bs(d)
    return f"{b.year:04d}{sep}{b.month:02d}{sep}{b.day:02d}"


def local_date(dt: datetime | None, tz=None) -> date | None:
    if dt is None:
        return None
    return timezone.localtime(dt, tz).date() if timezone.is_aware(dt) else dt.date()


def fy_start_year(d: date) -> int:
    b = to_bs(d)
    return b.year if b.month >= SHRAWAN else b.year - 1


def fiscal_year_label(d: date) -> str:
    """Internal key used in document numbers: '2083-84'."""
    y = fy_start_year(d)
    return f"{y}-{(y + 1) % 100:02d}"


def cbms_fiscal_year(label: str) -> str:
    """'2083-84' -> '2083.084' (CBMS format)."""
    start, end = label.split("-")
    return f"{start}.{int(end):03d}"


def fy_bounds(label: str) -> tuple[date, date]:
    """AD [start, end] dates of a fiscal year label."""
    start = int(label.split("-")[0])
    first = nd.date(start, SHRAWAN, 1).to_datetime_date()
    nxt = nd.date(start + 1, SHRAWAN, 1).to_datetime_date()
    return first, date.fromordinal(nxt.toordinal() - 1)


def bs_month_bounds(bs_year: int, bs_month: int) -> tuple[date, date]:
    first = nd.date(bs_year, bs_month, 1).to_datetime_date()
    ny, nm = (bs_year, bs_month + 1) if bs_month < 12 else (bs_year + 1, 1)
    nxt = nd.date(ny, nm, 1).to_datetime_date()
    return first, date.fromordinal(nxt.toordinal() - 1)


def fy_month(label: str, fy_month_no: int) -> tuple[int, int]:
    """Fiscal month 1 (Shrawan) .. 12 (Asar) -> (bs_year, bs_month)."""
    start = int(label.split("-")[0])
    m = SHRAWAN + fy_month_no - 1
    return (start, m) if m <= 12 else (start + 1, m - 12)


def aware_range(first: date, last: date, tz):
    """[first 00:00, last+1 00:00) in ``tz`` as aware datetimes for filtering."""
    start = datetime.combine(first, time.min, tzinfo=tz)
    end = datetime.combine(date.fromordinal(last.toordinal() + 1), time.min, tzinfo=tz)
    return start, end


def month_name(m: int) -> str:
    return MONTHS[m - 1]
