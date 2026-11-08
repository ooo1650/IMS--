/** Money is integer paisa everywhere; format only at the edge. */
const fmt = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function npr(paisa: number | null | undefined): string {
  if (paisa == null) return '—'
  return `Rs ${fmt.format((paisa || 0) / 100)}` // `|| 0` turns -0 into 0 (no "Rs -0.00")
}

export function plain(paisa: number): string {
  return fmt.format(paisa / 100)
}

/** "120.5" -> 12050. Returns null for invalid input. No floats involved. */
export function toPaisa(input: string): number | null {
  const s = input.replace(/,/g, '').trim()
  const m = /^(\d+)(?:\.(\d{0,2}))?$/.exec(s)
  if (!m) return null
  return Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0'))
}

export function toRupeesInput(paisa: number): string {
  return (paisa / 100).toFixed(2)
}

/** percent string "12.5" -> basis points 1250 */
export function toBasisPoints(input: string): number | null {
  const s = input.trim()
  const m = /^(\d+)(?:\.(\d{0,2}))?$/.exec(s)
  if (!m) return null
  const bp = Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0'))
  return bp <= 10000 ? bp : null
}
