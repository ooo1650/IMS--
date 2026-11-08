import { BitArray, Code128Reader, DecodeHintType, EAN13Reader } from '@zxing/library'
import { describe, expect, it } from 'vitest'
import { barcodeSvg, code128Modules, ean13CheckDigit, ean13Modules, generateInStoreEan13, isValidEan13, isValidUpcA, modulesFor } from './barcode'

/** Decode a module string with ZXing's reader, as a real scanner would see one row. */
function decode(modules: string, reader: Code128Reader | EAN13Reader) {
  const row = `0000000000${modules}0000000000`
  const bits = new BitArray(row.length)
  ;[...row].forEach((b, i) => b === '1' && bits.set(i))
  return reader.decodeRow(0, bits, new Map([[DecodeHintType.TRY_HARDER, true]])).getText()
}

describe('EAN-13', () => {
  it('computes check digits', () => {
    expect(ean13CheckDigit('400638133393')).toBe(1)
    expect(ean13CheckDigit('890100000019')).toBe(4)
    expect(isValidEan13('4006381333931')).toBe(true)
    expect(isValidEan13('4006381333932')).toBe(false)
    expect(isValidUpcA('036000291452')).toBe(true)
  })

  it('in-store codes start with 2 and are valid', () => {
    for (let i = 0; i < 50; i++) {
      const c = generateInStoreEan13()
      expect(c).toMatch(/^2\d{12}$/)
      expect(isValidEan13(c)).toBe(true)
    }
  })

  it.each(['4006381333931', '8901000000194', '2000000000015', '5901234123457'])('round-trips %s through a scanner decoder', (code) => {
    const m = ean13Modules(code)
    expect(m).toHaveLength(95)
    expect(decode(m, new EAN13Reader())).toBe(code)
  })

  it('UPC-A uses the EAN-13 symbol with a leading zero', () => {
    expect(modulesFor('036000291452').kind).toBe('ean13')
  })
})

describe('Code 128-B', () => {
  it.each(['MILK-1L', 'BREAD', 'SKU 42/a', 'KTM-2083-84-000001'])('round-trips %s', (text) => {
    expect(decode(code128Modules(text), new Code128Reader())).toBe(text)
  })

  it('falls back to Code 128 for non-EAN codes and rejects non-ASCII', () => {
    expect(modulesFor('ABC123').kind).toBe('code128')
    expect(modulesFor('4006381333932').kind).toBe('code128') // bad check digit → not EAN
    expect(() => code128Modules('दूध')).toThrow()
  })
})

it('renders an SVG with bars and the human-readable text', () => {
  const svg = barcodeSvg('4006381333931')
  expect(svg).toContain('<rect')
  expect(svg).toContain('4006381333931')
  expect(barcodeSvg('A<B', { showText: true })).toContain('A&lt;B')
})
