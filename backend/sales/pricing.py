"""Sale totals: discounts before tax, every tax summed per rate and rounded once
(design doc §9.2; payment.md Step 3).

Pure functions over integer paisa. Intermediate values are exact rationals
(``fractions.Fraction``) and are rounded half-up to paisa only when stored.

A line carries up to three charges on its *net* value (price before any tax):
  * excise  = net × e                      (excisable goods)
  * VAT     = net × (1 + e) × v            (VAT base includes excise)
  * levy    = net × l                      (HST or ESF on VAT-exempt services)
For tax-inclusive prices  net = base / ((1+e)(1+v) + l);  exclusive: net = base.
With e = l = 0 this is the familiar  VAT = base × v/(100+v)  for inclusive prices.

Each charge type is grouped by rate, rounded once per group, and the rounded
total is allocated back to lines in proportion to their unrounded amounts.

The frontend has a line-for-line port in ``frontend/src/lib/pricing.ts`` used
for offline checkout; ``sales/tests/pricing_vectors.json`` keeps them in sync.

Identity guaranteed on the result:
    subtotal - discount_total + tax_total + excise_total + hst_total + esf_total
      - digital_vat_discount + round_adjust == total
"""
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

NONE, AMOUNT, PERCENT = "none", "amount", "percent"
STANDARD, REDUCED, ZERO, EXEMPT = "standard", "reduced", "zero", "exempt"
HST, ESF = "hst", "esf"
ALLOCATION_METHOD = "proportional_largest_remainder"


def round_half_up(x: Fraction) -> int:
    """Round a rational to the nearest integer, ties away from zero."""
    if x >= 0:
        return int((x + Fraction(1, 2)) // 1)
    return -int((-x + Fraction(1, 2)) // 1)


def rate_fraction(rate) -> Fraction:
    return Fraction(Decimal(str(rate)))


def rate_key(r: Fraction) -> Decimal:
    return Decimal(r.numerator) / Decimal(r.denominator)


def discount_amount(base: int, dtype: str, value: int) -> int:
    """``value`` is paisa for AMOUNT, basis points (1000 = 10%) for PERCENT."""
    if dtype == NONE or not value:
        return 0
    if dtype == AMOUNT:
        amt = value
    elif dtype == PERCENT:
        if not 0 <= value <= 10000:
            raise ValueError("Percent discount must be between 0 and 100%")
        amt = round_half_up(Fraction(base * value, 10000))
    else:
        raise ValueError(f"Unknown discount type {dtype!r}")
    if amt < 0 or amt > base:
        raise ValueError("Discount cannot be negative or exceed the amount it applies to")
    return amt


def allocate(total: int, weights) -> list[int]:
    """Split integer ``total`` across ``weights`` (ints or Fractions)
    proportionally; shares sum exactly to total (largest remainder, ties broken
    by position)."""
    wsum = sum(weights, Fraction(0))
    if total == 0 or wsum == 0:
        return [0] * len(weights)
    exact = [Fraction(total) * w / wsum for w in weights]
    shares = [int(e // 1) for e in exact]
    remainder = total - sum(shares)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - shares[i]), i))
    for i in order[:remainder]:
        shares[i] += 1
    return shares


@dataclass
class LineInput:
    unit_price_paisa: int
    qty: int
    tax_rate: Decimal
    tax_inclusive: bool
    discount_type: str = NONE
    discount_value: int = 0
    tax_kind: str = STANDARD          # standard | reduced | zero | exempt
    excise_rate: Decimal = Decimal(0)  # percent
    levy_type: str = ""               # "" | hst | esf
    levy_rate: Decimal = Decimal(0)    # percent


@dataclass
class LineResult:
    gross_paisa: int
    line_discount_paisa: int
    sale_discount_share_paisa: int
    tax_amount_paisa: int
    line_total_paisa: int
    excise_paisa: int = 0
    levy_paisa: int = 0


@dataclass
class Totals:
    lines: list[LineResult] = field(default_factory=list)
    subtotal_paisa: int = 0
    discount_total_paisa: int = 0
    tax_total_paisa: int = 0
    excise_total_paisa: int = 0
    hst_total_paisa: int = 0
    esf_total_paisa: int = 0
    digital_vat_discount_paisa: int = 0
    round_adjust_paisa: int = 0
    total_paisa: int = 0
    tax_by_rate: dict = field(default_factory=dict)
    # CBMS reporting buckets (net of discounts, exact sums rounded once).
    taxable_sales_vat_paisa: int = 0
    excisable_amount_paisa: int = 0
    taxable_sales_hst_paisa: int = 0
    amount_for_esf_paisa: int = 0
    tax_exempted_sales_paisa: int = 0
    zero_rated_sales_paisa: int = 0
    allocation_method: str = ALLOCATION_METHOD

    def as_dict(self):
        return {
            "subtotal_paisa": self.subtotal_paisa,
            "discount_total_paisa": self.discount_total_paisa,
            "tax_total_paisa": self.tax_total_paisa,
            "excise_total_paisa": self.excise_total_paisa,
            "hst_total_paisa": self.hst_total_paisa,
            "esf_total_paisa": self.esf_total_paisa,
            "digital_vat_discount_paisa": self.digital_vat_discount_paisa,
            "round_adjust_paisa": self.round_adjust_paisa,
            "total_paisa": self.total_paisa,
            "taxable_sales_vat_paisa": self.taxable_sales_vat_paisa,
            "excisable_amount_paisa": self.excisable_amount_paisa,
            "taxable_sales_hst_paisa": self.taxable_sales_hst_paisa,
            "amount_for_esf_paisa": self.amount_for_esf_paisa,
            "tax_exempted_sales_paisa": self.tax_exempted_sales_paisa,
            "zero_rated_sales_paisa": self.zero_rated_sales_paisa,
            "tax_by_rate": {str(k): v for k, v in self.tax_by_rate.items()},
            "lines": [vars(l) for l in self.lines],
        }


def _grouped_round(amounts: list[Fraction], keys: list) -> tuple[list[int], dict]:
    """Round per group once; allocate each group's total to its members by
    their unrounded amount. Returns (per-line ints, {key: group total})."""
    groups: dict = {}
    for i, k in enumerate(keys):
        if k is not None:
            groups.setdefault(k, []).append(i)
    out, totals = [0] * len(amounts), {}
    for k, idxs in groups.items():
        group_total = round_half_up(sum((amounts[i] for i in idxs), Fraction(0)))
        totals[k] = group_total
        for i, share in zip(idxs, allocate(group_total, [amounts[i] for i in idxs])):
            out[i] = share
    return out, totals


def compute_totals(lines: list[LineInput], sale_discount_type=NONE, sale_discount_value=0, cash_rounding=1,
                   digital_vat_discount_bp=0) -> Totals:
    if not lines:
        return Totals()
    for l in lines:
        if l.qty <= 0:
            raise ValueError("Quantity must be positive")
        if l.unit_price_paisa < 0:
            raise ValueError("Price cannot be negative")

    gross = [l.unit_price_paisa * l.qty for l in lines]
    line_disc = [discount_amount(g, l.discount_type, l.discount_value) for g, l in zip(gross, lines)]
    after_line = [g - d for g, d in zip(gross, line_disc)]

    # Sale-level discount on the (price-terms) subtotal, spread over lines so
    # each line's taxable base reflects it — discount before tax.
    sale_disc = discount_amount(sum(after_line), sale_discount_type, sale_discount_value)
    shares = allocate(sale_disc, after_line)
    base = [a - s for a, s in zip(after_line, shares)]

    v = [rate_fraction(l.tax_rate) / 100 for l in lines]
    e = [rate_fraction(l.excise_rate) / 100 for l in lines]
    lv = [rate_fraction(l.levy_rate) / 100 if l.levy_type else Fraction(0) for l in lines]
    denom = [(1 + e[i]) * (1 + v[i]) + lv[i] for i in range(len(lines))]

    def net_of(amount, i):
        return Fraction(amount) / denom[i] if lines[i].tax_inclusive else Fraction(amount)

    net = [net_of(b, i) for i, b in enumerate(base)]
    vat_u = [net[i] * (1 + e[i]) * v[i] for i in range(len(lines))]
    exc_u = [net[i] * e[i] for i in range(len(lines))]
    levy_u = [net[i] * lv[i] for i in range(len(lines))]

    line_tax, tax_by_rate = _grouped_round(vat_u, [rate_key(v[i] * 100) for i in range(len(lines))])
    line_exc, exc_groups = _grouped_round(exc_u, [rate_key(e[i] * 100) if e[i] else None for i in range(len(lines))])
    line_levy, levy_groups = _grouped_round(
        levy_u, [(lines[i].levy_type, rate_key(lv[i] * 100)) if lv[i] else None for i in range(len(lines))])

    line_totals = [
        base[i] + (0 if lines[i].tax_inclusive else line_tax[i] + line_exc[i] + line_levy[i])
        for i in range(len(lines))
    ]

    subtotal = round_half_up(sum((net_of(g, i) for i, g in enumerate(gross)), Fraction(0)))
    discount = round_half_up(sum((net_of(line_disc[i] + shares[i], i) for i in range(len(lines))), Fraction(0)))
    tax_total = sum(tax_by_rate.values())
    excise_total = sum(exc_groups.values())
    hst_total = sum(t for (kind, _), t in levy_groups.items() if kind == HST)
    esf_total = sum(t for (kind, _), t in levy_groups.items() if kind == ESF)

    # Digital-payment VAT discount (OFF unless configured): a share of the VAT.
    digital = round_half_up(Fraction(tax_total * digital_vat_discount_bp, 10000)) if digital_vat_discount_bp else 0

    payable = sum(line_totals) - digital
    total = payable
    if cash_rounding and cash_rounding > 1:
        total = round_half_up(Fraction(payable, cash_rounding)) * cash_rounding
    charges = tax_total + excise_total + hst_total + esf_total
    round_adjust = total - (subtotal - discount + charges - digital)

    def bucket(pred, value=lambda i: net[i]):
        return round_half_up(sum((value(i) for i in range(len(lines)) if pred(i)), Fraction(0)))

    return Totals(
        lines=[LineResult(gross[i], line_disc[i], shares[i], line_tax[i], line_totals[i], line_exc[i], line_levy[i])
               for i in range(len(lines))],
        subtotal_paisa=subtotal,
        discount_total_paisa=discount,
        tax_total_paisa=tax_total,
        excise_total_paisa=excise_total,
        hst_total_paisa=hst_total,
        esf_total_paisa=esf_total,
        digital_vat_discount_paisa=digital,
        round_adjust_paisa=round_adjust,
        total_paisa=total,
        tax_by_rate=tax_by_rate,
        taxable_sales_vat_paisa=bucket(lambda i: v[i] > 0, lambda i: net[i] * (1 + e[i])),
        excisable_amount_paisa=bucket(lambda i: e[i] > 0),
        taxable_sales_hst_paisa=bucket(lambda i: lines[i].levy_type == HST and lv[i] > 0),
        amount_for_esf_paisa=bucket(lambda i: lines[i].levy_type == ESF and lv[i] > 0),
        tax_exempted_sales_paisa=bucket(lambda i: lines[i].tax_kind == EXEMPT and not lv[i]),
        zero_rated_sales_paisa=bucket(lambda i: lines[i].tax_kind == ZERO),
    )
