/**
 * Port of backend/sales/pricing.py — used for live cart totals and for
 * offline checkout, where the register must compute what the server would.
 * Exact rational arithmetic on BigInt; rounding half-up to paisa only at the
 * store step; tax summed per rate and rounded once (design doc §9.2).
 *
 * Kept in lock-step with the Python version by sales/tests/pricing_vectors.json
 * (see pricing.test.ts). Change both together.
 */

export type DiscountType = 'none' | 'amount' | 'percent'
export type TaxKind = 'standard' | 'reduced' | 'zero' | 'exempt'
export type LevyType = '' | 'hst' | 'esf'

interface LineInput {
  unit_price_paisa: number
  qty: number
  tax_rate: string // e.g. "13" or "12.50"
  tax_inclusive: boolean
  discount_type: DiscountType
  discount_value: number // paisa for amount, basis points for percent
  tax_kind?: TaxKind
  excise_rate?: string // percent
  levy_type?: LevyType
  levy_rate?: string // percent
}

interface LineResult {
  gross_paisa: number
  line_discount_paisa: number
  sale_discount_share_paisa: number
  tax_amount_paisa: number
  line_total_paisa: number
  excise_paisa: number
  levy_paisa: number
}

export interface Totals {
  subtotal_paisa: number
  discount_total_paisa: number
  tax_total_paisa: number
  excise_total_paisa: number
  hst_total_paisa: number
  esf_total_paisa: number
  digital_vat_discount_paisa: number
  round_adjust_paisa: number
  total_paisa: number
  taxable_sales_vat_paisa: number
  excisable_amount_paisa: number
  taxable_sales_hst_paisa: number
  amount_for_esf_paisa: number
  tax_exempted_sales_paisa: number
  zero_rated_sales_paisa: number
  lines: LineResult[]
  tax_by_rate: Record<string, number>
}

// --- tiny exact rational type ---------------------------------------------
interface Frac {
  n: bigint
  d: bigint
}

const abs = (x: bigint) => (x < 0n ? -x : x)
function gcd(a: bigint, b: bigint): bigint {
  a = abs(a)
  b = abs(b)
  while (b) [a, b] = [b, a % b]
  return a || 1n
}
function frac(n: bigint, d: bigint = 1n): Frac {
  if (d === 0n) throw new Error('division by zero')
  if (d < 0n) [n, d] = [-n, -d]
  const g = gcd(n, d)
  return { n: n / g, d: d / g }
}
const add = (a: Frac, b: Frac) => frac(a.n * b.d + b.n * a.d, a.d * b.d)
const sub = (a: Frac, b: Frac) => frac(a.n * b.d - b.n * a.d, a.d * b.d)
const mul = (a: Frac, b: Frac) => frac(a.n * b.n, a.d * b.d)
const div = (a: Frac, b: Frac) => frac(a.n * b.d, a.d * b.n)
const cmp = (a: Frac, b: Frac) => {
  const l = a.n * b.d
  const r = b.n * a.d
  return l < r ? -1 : l > r ? 1 : 0
}
const int = (x: number | bigint) => frac(BigInt(x))
/** floor for any sign */
function floor(x: Frac): bigint {
  const q = x.n / x.d
  return x.n < 0n && q * x.d !== x.n ? q - 1n : q
}
const HALF = frac(1n, 2n)

function roundHalfUp(x: Frac): bigint {
  if (cmp(x, int(0)) >= 0) return floor(add(x, HALF))
  return -floor(add(frac(-x.n, x.d), HALF))
}

function parseRate(rate: string): Frac {
  const s = String(rate).trim()
  const m = /^(\d+)(?:\.(\d+))?$/.exec(s)
  if (!m) throw new Error(`bad tax rate ${rate}`)
  const decimals = m[2] ?? ''
  return frac(BigInt(m[1] + decimals), 10n ** BigInt(decimals.length))
}

function rateKey(r: Frac): string {
  // Matches Python's normalised Decimal str: "13", "12.5"
  const whole = r.n / r.d
  let rem = r.n % r.d
  if (rem === 0n) return whole.toString()
  let digits = ''
  for (let i = 0; i < 6 && rem; i++) {
    rem *= 10n
    digits += (rem / r.d).toString()
    rem %= r.d
  }
  return `${whole}.${digits}`
}

function discountAmount(base: number, dtype: DiscountType, value: number): number {
  if (dtype === 'none' || !value) return 0
  let amt: number
  if (dtype === 'amount') amt = value
  else if (dtype === 'percent') {
    if (value < 0 || value > 10000) throw new Error('Percent discount must be between 0 and 100%')
    amt = Number(roundHalfUp(frac(BigInt(base) * BigInt(value), 10000n)))
  } else throw new Error(`Unknown discount type ${dtype}`)
  if (amt < 0 || amt > base) throw new Error('Discount cannot be negative or exceed the amount it applies to')
  return amt
}

function allocate(total: number, weights: Frac[]): number[] {
  const wsum = weights.reduce(add, int(0))
  if (total === 0 || wsum.n === 0n) return weights.map(() => 0)
  const exact = weights.map((w) => div(mul(int(total), w), wsum))
  const shares = exact.map(floor)
  let remainder = BigInt(total) - shares.reduce((a, b) => a + b, 0n)
  const order = exact
    .map((e, i) => ({ i, f: sub(e, int(shares[i])) }))
    .sort((a, b) => -cmp(a.f, b.f) || a.i - b.i)
  for (const { i } of order) {
    if (remainder <= 0n) break
    shares[i] += 1n
    remainder -= 1n
  }
  return shares.map(Number)
}

/** Round per group once; allocate each group's total to members by unrounded amount. */
function groupedRound(amounts: Frac[], keys: (string | null)[]): [number[], Map<string, number>] {
  const groups = new Map<string, number[]>()
  keys.forEach((k, i) => {
    if (k === null) return
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k)!.push(i)
  })
  const out = amounts.map(() => 0)
  const totals = new Map<string, number>()
  for (const [k, idxs] of groups) {
    const groupTotal = Number(roundHalfUp(idxs.map((i) => amounts[i]).reduce(add, int(0))))
    totals.set(k, groupTotal)
    allocate(groupTotal, idxs.map((i) => amounts[i])).forEach((share, j) => {
      out[idxs[j]] = share
    })
  }
  return [out, totals]
}

const EMPTY: Totals = {
  subtotal_paisa: 0, discount_total_paisa: 0, tax_total_paisa: 0, excise_total_paisa: 0, hst_total_paisa: 0,
  esf_total_paisa: 0, digital_vat_discount_paisa: 0, round_adjust_paisa: 0, total_paisa: 0,
  taxable_sales_vat_paisa: 0, excisable_amount_paisa: 0, taxable_sales_hst_paisa: 0, amount_for_esf_paisa: 0,
  tax_exempted_sales_paisa: 0, zero_rated_sales_paisa: 0, lines: [], tax_by_rate: {},
}

export function computeTotals(
  lines: LineInput[],
  saleDiscountType: DiscountType = 'none',
  saleDiscountValue = 0,
  cashRounding = 1,
  digitalVatDiscountBp = 0,
): Totals {
  if (!lines.length) return { ...EMPTY, lines: [], tax_by_rate: {} }
  for (const l of lines) {
    if (l.qty <= 0) throw new Error('Quantity must be positive')
    if (l.unit_price_paisa < 0) throw new Error('Price cannot be negative')
  }
  const n = lines.length
  const idx = [...Array(n).keys()]
  const gross = lines.map((l) => l.unit_price_paisa * l.qty)
  const lineDisc = lines.map((l, i) => discountAmount(gross[i], l.discount_type, l.discount_value))
  const afterLine = gross.map((g, i) => g - lineDisc[i])

  const saleDisc = discountAmount(afterLine.reduce((a, b) => a + b, 0), saleDiscountType, saleDiscountValue)
  const shares = allocate(saleDisc, afterLine.map(int))
  const base = afterLine.map((a, i) => a - shares[i])

  const HUNDRED = int(100)
  const v = lines.map((l) => div(parseRate(l.tax_rate), HUNDRED))
  const e = lines.map((l) => div(parseRate(l.excise_rate ?? '0'), HUNDRED))
  const lv = lines.map((l) => (l.levy_type ? div(parseRate(l.levy_rate ?? '0'), HUNDRED) : int(0)))
  const one = int(1)
  const denom = idx.map((i) => add(mul(add(one, e[i]), add(one, v[i])), lv[i]))
  const netOf = (amount: number, i: number) => (lines[i].tax_inclusive ? div(int(amount), denom[i]) : int(amount))

  const net = base.map((b, i) => netOf(b, i))
  const vatU = idx.map((i) => mul(mul(net[i], add(one, e[i])), v[i]))
  const excU = idx.map((i) => mul(net[i], e[i]))
  const levyU = idx.map((i) => mul(net[i], lv[i]))
  const isZero = (f: Frac) => f.n === 0n

  const [lineTax, taxGroups] = groupedRound(vatU, idx.map((i) => rateKey(mul(v[i], HUNDRED))))
  const [lineExc, excGroups] = groupedRound(excU, idx.map((i) => (isZero(e[i]) ? null : rateKey(mul(e[i], HUNDRED)))))
  const [lineLevy, levyGroups] = groupedRound(
    levyU,
    idx.map((i) => (isZero(lv[i]) ? null : `${lines[i].levy_type}|${rateKey(mul(lv[i], HUNDRED))}`)),
  )
  const lineTotals = idx.map((i) => base[i] + (lines[i].tax_inclusive ? 0 : lineTax[i] + lineExc[i] + lineLevy[i]))

  const sumRound = (fs: Frac[]) => Number(roundHalfUp(fs.reduce(add, int(0))))
  const subtotal = sumRound(gross.map((g, i) => netOf(g, i)))
  const discount = sumRound(idx.map((i) => netOf(lineDisc[i] + shares[i], i)))
  const sumMap = (m: Map<string, number>, pred: (k: string) => boolean = () => true) =>
    [...m].reduce((a, [k, t]) => (pred(k) ? a + t : a), 0)
  const taxTotal = sumMap(taxGroups)
  const exciseTotal = sumMap(excGroups)
  const hstTotal = sumMap(levyGroups, (k) => k.startsWith('hst|'))
  const esfTotal = sumMap(levyGroups, (k) => k.startsWith('esf|'))
  const digital = digitalVatDiscountBp ? Number(roundHalfUp(frac(BigInt(taxTotal) * BigInt(digitalVatDiscountBp), 10000n))) : 0

  const payable = lineTotals.reduce((a, b) => a + b, 0) - digital
  let total = payable
  if (cashRounding > 1) total = Number(roundHalfUp(frac(BigInt(payable), BigInt(cashRounding)))) * cashRounding
  const charges = taxTotal + exciseTotal + hstTotal + esfTotal
  const bucket = (pred: (i: number) => boolean, value: (i: number) => Frac = (i) => net[i]) =>
    sumRound(idx.filter(pred).map(value))

  return {
    subtotal_paisa: subtotal,
    discount_total_paisa: discount,
    tax_total_paisa: taxTotal,
    excise_total_paisa: exciseTotal,
    hst_total_paisa: hstTotal,
    esf_total_paisa: esfTotal,
    digital_vat_discount_paisa: digital,
    round_adjust_paisa: total - (subtotal - discount + charges - digital),
    total_paisa: total,
    taxable_sales_vat_paisa: bucket((i) => !isZero(v[i]), (i) => mul(net[i], add(one, e[i]))),
    excisable_amount_paisa: bucket((i) => !isZero(e[i])),
    taxable_sales_hst_paisa: bucket((i) => lines[i].levy_type === 'hst' && !isZero(lv[i])),
    amount_for_esf_paisa: bucket((i) => lines[i].levy_type === 'esf' && !isZero(lv[i])),
    tax_exempted_sales_paisa: bucket((i) => lines[i].tax_kind === 'exempt' && isZero(lv[i])),
    zero_rated_sales_paisa: bucket((i) => lines[i].tax_kind === 'zero'),
    tax_by_rate: Object.fromEntries(taxGroups),
    lines: idx.map((i) => ({
      gross_paisa: gross[i],
      line_discount_paisa: lineDisc[i],
      sale_discount_share_paisa: shares[i],
      tax_amount_paisa: lineTax[i],
      line_total_paisa: lineTotals[i],
      excise_paisa: lineExc[i],
      levy_paisa: lineLevy[i],
    })),
  }
}
