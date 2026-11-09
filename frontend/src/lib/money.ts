/** Money is integer paisa everywhere; format only at the edge. */
const fmt = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function npr(paisa: number | null | undefined): string {
  if (paisa == null) return '—'
  return `Rs ${fmt.format((paisa || 0) / 100)}` // `|| 0` turns -0 into 0 (no "Rs -0.00")
}

export function plain(paisa: number): string {
  return fmt.format(paisa / 100)
}

const DEVANAGARI_DIGITS = '०१२३४५६७८९'
/** Largest amount anyone types: Rs 100 billion (the server's limit too). */
const MAX_PAISA = 10 ** 13

/**
 * How people actually type numbers at a Nepali counter, made plain:
 * Nepali digits (५००) become 500; "Rs", "Rs.", "NPR", "रु"/"रू" in front,
 * "/-" or "%" behind, spaces and thousands commas are dropped.
 */
export function normalizeNumber(input: string): string {
  return input
    .replace(/[०-९]/g, (d) => String(DEVANAGARI_DIGITS.indexOf(d)))
    .replace(/^\s*(?:rs\.?|npr|रु\.?|रू\.?)\s*/i, '')
    .replace(/\s*(?:\/-|%)\s*$/, '')
    .replace(/[,\s]/g, '')
}

/** Why an amount in rupees can't be used, or null if it's fine (or empty). */
export function moneyProblem(input: string): string | null {
  const s = normalizeNumber(input)
  if (s === '') return null
  if (s.startsWith('-')) return "Can't be negative"
  if (!/^\d*\.?\d*$/.test(s) || s === '.') return 'Enter an amount in rupees, like 120 or 120.50'
  if ((s.split('.')[1] ?? '').length > 2) return 'At most 2 decimal places (paisa)'
  if (toPaisa(s) == null) return 'That amount is too large'
  return null
}

/** "120.5", "Rs 120.50", "१२०.५", "120/-" -> 12050. Returns null for invalid input. No floats involved. */
export function toPaisa(input: string): number | null {
  const s = normalizeNumber(input)
  const m = /^(\d*)(?:\.(\d{0,2}))?$/.exec(s)
  if (!m || (m[1] === '' && !m[2])) return null
  const paisa = Number(m[1] || '0') * 100 + Number((m[2] ?? '').padEnd(2, '0'))
  return paisa <= MAX_PAISA ? paisa : null
}

export function toRupeesInput(paisa: number): string {
  return (paisa / 100).toFixed(2)
}

/** percent string "12.5" (or "12.5%", "१२.५") -> basis points 1250; null if invalid or over 100 */
export function toBasisPoints(input: string): number | null {
  const s = normalizeNumber(input)
  const m = /^(\d*)(?:\.(\d{0,2}))?$/.exec(s)
  if (!m || (m[1] === '' && !m[2])) return null
  const bp = Number(m[1] || '0') * 100 + Number((m[2] ?? '').padEnd(2, '0'))
  return bp <= 10000 ? bp : null
}

/** Why a percentage can't be used, or null if it's fine (or empty). */
export function percentProblem(input: string): string | null {
  const s = normalizeNumber(input)
  if (s === '') return null
  if (s.startsWith('-')) return "Can't be negative"
  if (!/^\d*\.?\d*$/.test(s) || s === '.') return 'Enter a percentage, like 10 or 12.5'
  if ((s.split('.')[1] ?? '').length > 2) return 'At most 2 decimal places'
  if (toBasisPoints(s) == null) return "Can't be more than 100%"
  return null
}

/** A whole number typed by a person ("12", "१२", " 12 "): the number, or null. */
export function parseWhole(input: string): number | null {
  const s = normalizeNumber(input)
  return /^-?\d+$/.test(s) && Math.abs(Number(s)) <= Number.MAX_SAFE_INTEGER ? Number(s) : null
}

/** Why a whole number can't be used (empty counts as missing), or null. */
export function wholeProblem(input: string, { min = 0, max = 10_000_000 }: { min?: number; max?: number } = {}): string | null {
  const s = normalizeNumber(input)
  if (s === '') return 'Enter a number'
  if (/^-?\d*[.,]\d+$/.test(input.trim()) || /\./.test(s)) return 'Whole numbers only (no decimals)'
  const n = parseWhole(s)
  if (n == null) return 'Enter a whole number'
  if (n < min) return min === 0 ? "Can't be negative" : `At least ${min}`
  if (n > max) return `At most ${max.toLocaleString('en-IN')}`
  return null
}
