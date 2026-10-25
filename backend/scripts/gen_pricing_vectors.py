"""Regenerate sales/tests/pricing_vectors.json (shared by the Python and
TypeScript pricing tests). Run from backend/:  python scripts/gen_pricing_vectors.py
Only regenerate after checking the engine still matches the old vectors."""
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sales.pricing import LineInput, compute_totals  # noqa: E402

rng = random.Random(7)
FIXED = [
    ("exclusive_three", [(1000, 1, "13", False, "none", 0)] * 3, "none", 0, 1, 0),
    ("inclusive_round_once", [(100, 1, "13", True, "none", 0)] * 3, "none", 0, 1, 0),
    ("line_pct", [(1000, 1, "13", False, "percent", 1000)], "none", 0, 1, 0),
    ("sale_amount", [(300, 1, "13", False, "none", 0), (100, 1, "13", False, "none", 0)], "amount", 100, 1, 0),
    ("cash_round", [(1000, 1, "13", False, "none", 0)] * 3, "none", 0, 100, 0),
    ("inclusive_sale_disc", [(11300, 1, "13", True, "none", 0)], "amount", 1130, 1, 0),
    ("digital_discount", [(1000, 1, "13", False, "none", 0)] * 3, "none", 0, 1, 1000),
]
PROFILES = [  # (rate, kind, excise, levy, levy_rate)
    ("13", "standard", "0", "", "0"), ("13", "standard", "0", "", "0"), ("5", "reduced", "0", "", "0"),
    ("0", "zero", "0", "", "0"), ("0", "exempt", "0", "", "0"), ("13", "standard", "10", "", "0"),
    ("0", "exempt", "0", "hst", "5"), ("12.5", "standard", "0", "", "0"),
]


def line(p, q, rate, incl, dt, dv, kind="standard", excise="0", levy="", levy_rate="0"):
    return dict(unit_price_paisa=p, qty=q, tax_rate=rate, tax_inclusive=incl, discount_type=dt, discount_value=dv,
                tax_kind=kind, excise_rate=excise, levy_type=levy, levy_rate=levy_rate)


cases = [(n, [line(*l) for l in ls], sdt, sdv, cr, dbp) for n, ls, sdt, sdv, cr, dbp in FIXED]
for i in range(60):
    ls = []
    for _ in range(rng.randint(1, 6)):
        dt = rng.choice(["none", "none", "amount", "percent"])
        price, qty = rng.randint(1, 200000), rng.randint(1, 7)
        dv = 0 if dt == "none" else (rng.randint(0, price * qty) if dt == "amount" else rng.randint(0, 5000))
        rate, kind, excise, levy, lr = rng.choice(PROFILES)
        ls.append(line(price, qty, rate, rng.random() < 0.6, dt, dv, kind, excise, levy, lr))
    sdt = rng.choice(["none", "amount", "percent"])
    sdv = 0 if sdt == "none" else (rng.randint(0, 1000) if sdt == "amount" else rng.randint(0, 3000))
    cases.append((f"random_{i}", ls, sdt, sdv, rng.choice([1, 1, 100]), rng.choice([0, 0, 1000])))

out = []
for name, ls, sdt, sdv, cr, dbp in cases:
    inp = [LineInput(l["unit_price_paisa"], l["qty"], Decimal(l["tax_rate"]), l["tax_inclusive"], l["discount_type"],
                     l["discount_value"], l["tax_kind"], Decimal(l["excise_rate"]), l["levy_type"],
                     Decimal(l["levy_rate"])) for l in ls]
    try:
        t = compute_totals(inp, sdt, sdv, cr, dbp)
    except ValueError:
        continue
    d = t.as_dict()
    d.pop("tax_by_rate")
    out.append({"name": name, "lines": ls, "sale_discount_type": sdt, "sale_discount_value": sdv, "cash_rounding": cr,
                "digital_vat_discount_bp": dbp, "expected": d})
path = Path(__file__).resolve().parent.parent / "sales/tests/pricing_vectors.json"
path.write_text(json.dumps(out, indent=1))
print(f"wrote {len(out)} vectors to {path}")
