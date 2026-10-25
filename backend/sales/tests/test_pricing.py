import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from django.test import SimpleTestCase

from sales.pricing import AMOUNT, PERCENT, LineInput, allocate, compute_totals, round_half_up

VECTORS = Path(__file__).with_name("pricing_vectors.json")


def L(price, qty=1, rate="13", incl=False, dtype="none", dval=0, kind="standard", excise="0", levy="", levy_rate="0"):
    return LineInput(price, qty, Decimal(rate), incl, dtype, dval, kind, Decimal(excise), levy, Decimal(levy_rate))


def assert_identity(tc, t):
    charges = t.tax_total_paisa + t.excise_total_paisa + t.hst_total_paisa + t.esf_total_paisa
    tc.assertEqual(t.subtotal_paisa - t.discount_total_paisa + charges - t.digital_vat_discount_paisa
                   + t.round_adjust_paisa, t.total_paisa)


class RoundingTests(SimpleTestCase):
    def test_round_half_up(self):
        self.assertEqual(round_half_up(Fraction(5, 2)), 3)
        self.assertEqual(round_half_up(Fraction(3, 2)), 2)
        self.assertEqual(round_half_up(Fraction(-5, 2)), -3)
        self.assertEqual(round_half_up(Fraction(249, 100)), 2)

    def test_allocate_sums_exactly(self):
        self.assertEqual(allocate(100, [1, 1, 1]), [34, 33, 33])
        self.assertEqual(sum(allocate(35, [100, 100, 100])), 35)
        self.assertEqual(allocate(0, [5, 5]), [0, 0])
        self.assertEqual(allocate(7, [0, 0]), [0, 0])


class TotalsTests(SimpleTestCase):
    def test_exclusive_tax(self):
        t = compute_totals([L(1000), L(1000), L(1000)])
        self.assertEqual((t.subtotal_paisa, t.tax_total_paisa, t.total_paisa), (3000, 390, 3390))
        assert_identity(self, t)

    def test_inclusive_tax_rounds_once_per_rate_not_per_line(self):
        # Each line's tax is 100*13/113 = 11.504…; per-line rounding would give 36.
        t = compute_totals([L(100, incl=True), L(100, incl=True), L(100, incl=True)])
        self.assertEqual(t.tax_total_paisa, 35)
        self.assertEqual(t.total_paisa, 300)
        self.assertEqual(sum(l.tax_amount_paisa for l in t.lines), 35)
        assert_identity(self, t)

    def test_line_percent_discount_before_tax(self):
        t = compute_totals([L(1000, dtype=PERCENT, dval=1000)])  # 10%
        self.assertEqual((t.discount_total_paisa, t.tax_total_paisa, t.total_paisa), (100, 117, 1017))
        assert_identity(self, t)

    def test_sale_discount_spread_over_lines(self):
        t = compute_totals([L(300), L(100)], AMOUNT, 100)
        self.assertEqual([l.sale_discount_share_paisa for l in t.lines], [75, 25])
        self.assertEqual((t.tax_total_paisa, t.total_paisa), (39, 339))
        assert_identity(self, t)

    def test_mixed_rates_grouped(self):
        t = compute_totals([L(1000, rate="13"), L(500, rate="0"), L(1000, rate="13")])
        self.assertEqual(t.tax_by_rate, {Decimal("13"): 260, Decimal("0"): 0})
        self.assertEqual(t.total_paisa, 2760)

    def test_cash_rounding_goes_to_round_adjust(self):
        t = compute_totals([L(1000), L(1000), L(1000)], cash_rounding=100)
        self.assertEqual(t.total_paisa, 3400)
        self.assertEqual(t.round_adjust_paisa, 10)
        assert_identity(self, t)

    def test_inclusive_with_discount_identity(self):
        t = compute_totals([L(11300, incl=True)], AMOUNT, 1130)
        self.assertEqual((t.subtotal_paisa, t.discount_total_paisa, t.tax_total_paisa, t.total_paisa),
                         (10000, 1000, 1170, 10170))
        assert_identity(self, t)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            compute_totals([L(100, qty=0)])
        with self.assertRaises(ValueError):
            compute_totals([L(100, dtype=AMOUNT, dval=101)])
        with self.assertRaises(ValueError):
            compute_totals([L(100, dtype=PERCENT, dval=10001)])

    def test_excise_is_inside_the_vat_base(self):
        # net 1000, excise 10% = 100, VAT 13% on 1100 = 143
        for incl, price in ((False, 1000), (True, 1243)):
            t = compute_totals([L(price, incl=incl, excise="10")])
            self.assertEqual((t.excise_total_paisa, t.tax_total_paisa, t.total_paisa), (100, 143, 1243))
            self.assertEqual((t.excisable_amount_paisa, t.taxable_sales_vat_paisa), (1000, 1100))
            assert_identity(self, t)

    def test_hst_levy_on_exempt_service(self):
        t = compute_totals([L(1050, rate="0", incl=True, kind="exempt", levy="hst", levy_rate="5")])
        self.assertEqual((t.hst_total_paisa, t.tax_total_paisa, t.taxable_sales_hst_paisa), (50, 0, 1000))
        self.assertEqual(t.tax_exempted_sales_paisa, 0)  # reported as HST sales, not plain exempt
        assert_identity(self, t)

    def test_zero_rated_and_exempt_reported_separately(self):
        t = compute_totals([L(500, rate="0", kind="zero"), L(300, rate="0", kind="exempt"), L(1000)])
        self.assertEqual((t.zero_rated_sales_paisa, t.tax_exempted_sales_paisa, t.taxable_sales_vat_paisa),
                         (500, 300, 1000))

    def test_digital_vat_discount_is_a_share_of_vat(self):
        t = compute_totals([L(1000), L(1000), L(1000)], digital_vat_discount_bp=1000)
        self.assertEqual((t.tax_total_paisa, t.digital_vat_discount_paisa, t.total_paisa), (390, 39, 3351))
        assert_identity(self, t)

    def test_shared_vectors(self):
        """Same file is checked by the frontend's pricing.ts test."""
        for case in json.loads(VECTORS.read_text()):
            lines = [L(l["unit_price_paisa"], l["qty"], l["tax_rate"], l["tax_inclusive"], l["discount_type"],
                       l["discount_value"], l["tax_kind"], l["excise_rate"], l["levy_type"], l["levy_rate"])
                     for l in case["lines"]]
            t = compute_totals(lines, case["sale_discount_type"], case["sale_discount_value"], case["cash_rounding"],
                               case["digital_vat_discount_bp"])
            got = {k: v for k, v in t.as_dict().items() if k != "tax_by_rate"}
            self.assertEqual(got, case["expected"], case["name"])
            assert_identity(self, t)
