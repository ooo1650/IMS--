import { describe, expect, it } from 'vitest'
import vectors from '../../../backend/sales/tests/pricing_vectors.json'
import { computeTotals, type DiscountType, type LevyType, type TaxKind } from './pricing'

describe('pricing parity with backend/sales/pricing.py', () => {
  for (const c of vectors) {
    it(c.name, () => {
      const t = computeTotals(
        c.lines.map((l) => ({
          ...l, discount_type: l.discount_type as DiscountType, tax_kind: l.tax_kind as TaxKind, levy_type: l.levy_type as LevyType,
        })),
        c.sale_discount_type as DiscountType,
        c.sale_discount_value,
        c.cash_rounding,
        c.digital_vat_discount_bp,
      )
      const { tax_by_rate: _ignored, ...got } = t
      void _ignored
      expect(got).toEqual(c.expected)
      const charges = t.tax_total_paisa + t.excise_total_paisa + t.hst_total_paisa + t.esf_total_paisa
      expect(t.subtotal_paisa - t.discount_total_paisa + charges - t.digital_vat_discount_paisa + t.round_adjust_paisa).toBe(t.total_paisa)
    })
  }

  it('rounds inclusive tax once per rate', () => {
    const line = { unit_price_paisa: 100, qty: 1, tax_rate: '13', tax_inclusive: true, discount_type: 'none' as const, discount_value: 0 }
    expect(computeTotals([line, line, line]).tax_total_paisa).toBe(35)
  })
})
