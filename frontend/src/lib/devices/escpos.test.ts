import { describe, expect, it } from 'vitest'
import { EscPos, poleDisplayBytes, toPrinterText } from './escpos'

const bytes = (b: Uint8Array) => [...b]

describe('toPrinterText', () => {
  it('keeps ASCII and transliterates typography', () => {
    expect(toPrinterText('Café — 2×3 “ok”')).toBe('Cafe - 2x3 "ok"')
  })
  it('replaces scripts thermal fonts lack', () => {
    expect(toPrinterText('दूध 1L')).toMatch(/^\?+ 1L$/)
  })
})

describe('EscPos', () => {
  it('starts with init and encodes text as single bytes', () => {
    expect(bytes(new EscPos().line('Hi').build())).toEqual([0x1b, 0x40, 0x48, 0x69, 0x0a])
  })

  it('emits GS k 73 for CODE128-B with the length prefix', () => {
    const b = bytes(new EscPos().barcode128('KTM-1').build())
    const k = b.indexOf(0x6b)
    expect(b.slice(k - 1, k + 3)).toEqual([0x1d, 0x6b, 73, 7]) // "{B" + 5 chars
    expect(String.fromCharCode(...b.slice(k + 3, k + 10))).toBe('{BKTM-1')
  })

  it('ends with cut then drawer pulse when asked', () => {
    const b = bytes(new EscPos().cut().drawer().build())
    expect(b.slice(-12)).toEqual([0x0a, 0x0a, 0x0a, 0x1d, 0x56, 0x42, 0x00, 0x1b, 0x70, 0x00, 0x19, 0xfa])
  })
})

it('pole display: clear, then two 20-char lines', () => {
  const b = poleDisplayBytes('Milk 1L', 'TOTAL Rs 110.00')
  expect(bytes(b).slice(0, 3)).toEqual([0x1b, 0x40, 0x0c])
  const text = String.fromCharCode(...b.slice(3))
  expect(text).toHaveLength(40)
  expect(text.slice(0, 20)).toBe('Milk 1L'.padEnd(20))
  expect(text.slice(20)).toBe('TOTAL Rs 110.00'.padEnd(20))
})
