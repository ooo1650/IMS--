import { describe, expect, it } from 'vitest'
import { poleLines } from './display'
import { WedgeDetector, diagnoseScan } from './scanner'

function type(det: WedgeDetector, text: string, gap: number, start = 1000) {
  let t = start
  let out: string | null = null
  for (const k of [...text, 'Enter']) {
    out = det.feed(k === 'Enter' ? 'Enter' : k, t) ?? out
    t += gap
  }
  return out
}

describe('WedgeDetector', () => {
  it('recognises a fast burst ending in Enter', () => {
    expect(type(new WedgeDetector(), '8901000000194', 8)).toBe('8901000000194')
  })
  it('ignores human typing speed', () => {
    expect(type(new WedgeDetector(), 'milk', 150)).toBeNull()
  })
  it('ignores short bursts (a stray Enter)', () => {
    expect(type(new WedgeDetector(), 'ab', 5)).toBeNull()
  })
  it('starts fresh after a pause', () => {
    const det = new WedgeDetector()
    det.feed('x', 0)
    expect(type(det, 'ABC-123', 5, 5000)).toBe('ABC-123')
  })
})

describe('diagnoseScan', () => {
  const fast = [0, 8, 16, 24, 32]
  it('passes a fast code ending in Enter', () => {
    expect(diagnoseScan('8901', fast, 'Enter').ok).toBe(true)
  })
  it('names a missing or wrong suffix', () => {
    expect(diagnoseScan('8901', fast, null).message).toMatch(/sends nothing/)
    expect(diagnoseScan('8901', fast, 'Tab').message).toMatch(/sends Tab/)
  })
  it('names a keystroke delay', () => {
    expect(diagnoseScan('8901', [0, 120, 240, 360, 480], 'Enter').message).toMatch(/slowly/)
  })
  it('names a non-English keyboard layout', () => {
    expect(diagnoseScan('८९०१', fast, 'Enter').message).toMatch(/keyboard layout/)
  })
  it('names a partial code', () => {
    expect(diagnoseScan('89', fast, 'Enter').message).toMatch(/Only 2/)
  })
  it('agrees with the detector the POS uses', () => {
    for (const gap of [8, 50, 51, 150]) {
      const times = fast.map((_, i) => 1000 + i * gap)
      expect(diagnoseScan('8901', times, 'Enter').ok).toBe(type(new WedgeDetector(), '8901', gap) === '8901')
    }
  })
})

describe('pole display text', () => {
  const base = { store: 'KTM', lines: [{ name: 'Fresh Milk 1L', qty: 2, amount: '220.00' }], total: '220.00' }
  it('shows the last item and the running total', () => {
    const [a, b] = poleLines({ ...base, mode: 'cart' }, 'WELCOME')
    expect(a).toBe('Fresh Milk 1L 220.00')
    expect(b).toMatch(/^TOTAL +Rs 220\.00$/)
    expect(b).toHaveLength(20)
    expect(a.length).toBeLessThanOrEqual(20)
  })
  it('thanks and shows change when done', () => {
    const [a, b] = poleLines({ ...base, mode: 'done', change: '30.00' }, 'WELCOME')
    expect(a).toBe('THANK YOU!')
    expect(b).toMatch(/^CHANGE +Rs 30\.00$/)
    expect(b).toHaveLength(20)
  })
  it('idle shows the welcome text', () => {
    expect(poleLines({ ...base, mode: 'idle' }, 'NAMASTE')[0]).toBe('NAMASTE')
  })
})
