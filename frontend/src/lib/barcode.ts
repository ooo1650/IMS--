/**
 * Barcode helpers for scanning and shelf/product labels.
 *  - EAN-13 / UPC-A check digits and validation
 *  - in-store codes: EAN-13 with prefix "2" (GS1 range 20–29 is reserved for
 *    internal use, so these never clash with manufacturer barcodes)
 *  - SVG rendering of EAN-13 and Code 128 (B) for printing labels
 */

export function ean13CheckDigit(first12: string): number {
  let sum = 0
  for (let i = 0; i < 12; i++) sum += Number(first12[i]) * (i % 2 ? 3 : 1)
  return (10 - (sum % 10)) % 10
}

export function isValidEan13(code: string): boolean {
  return /^\d{13}$/.test(code) && ean13CheckDigit(code.slice(0, 12)) === Number(code[12])
}

/** UPC-A is EAN-13 with a leading zero. */
export function isValidUpcA(code: string): boolean {
  return /^\d{12}$/.test(code) && isValidEan13(`0${code}`)
}

/** A random in-store EAN-13 ("2" + 11 digits + check). Uniqueness is enforced by the server. */
export function generateInStoreEan13(random: () => number = Math.random): string {
  let body = '2'
  for (let i = 0; i < 11; i++) body += Math.floor(random() * 10)
  return body + ean13CheckDigit(body)
}

// --- EAN-13 ---------------------------------------------------------------------

const L = ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011']
const G = L.map((p) => [...p].reverse().map((b) => (b === '1' ? '0' : '1')).join(''))
const R = L.map((p) => [...p].map((b) => (b === '1' ? '0' : '1')).join(''))
const PARITY = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL']

/** Module pattern ("1" = bar) for a valid EAN-13. */
export function ean13Modules(code: string): string {
  if (!isValidEan13(code)) throw new Error(`Not a valid EAN-13: ${code}`)
  const d = [...code].map(Number)
  const parity = PARITY[d[0]]
  let out = '101'
  for (let i = 1; i <= 6; i++) out += (parity[i - 1] === 'L' ? L : G)[d[i]]
  out += '01010'
  for (let i = 7; i <= 12; i++) out += R[d[i]]
  return out + '101'
}

// --- Code 128 (set B: printable ASCII) --------------------------------------------

const C128 = [
  '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
  '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
  '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
  '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
  '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
  '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
  '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
  '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
  '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
  '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
  '114131', '311141', '411131', '211412', '211214', '211232', '2331112',
]
const START_B = 104
const STOP = 106

/** Module pattern for Code 128-B; throws on characters outside printable ASCII. */
export function code128Modules(text: string): string {
  const values = [...text].map((ch) => {
    const v = ch.charCodeAt(0) - 32
    if (v < 0 || v > 94) throw new Error(`Code 128-B can't encode "${ch}"`)
    return v
  })
  const check = values.reduce((sum, v, i) => sum + v * (i + 1), START_B) % 103
  let out = ''
  for (const v of [START_B, ...values, check, STOP]) {
    // Widths alternate bar/space, starting with a bar.
    ;[...C128[v]].forEach((w, i) => (out += (i % 2 ? '0' : '1').repeat(Number(w))))
  }
  return out
}

/** Best symbology for a stored barcode: EAN-13/UPC-A when valid, else Code 128. */
export function modulesFor(code: string): { modules: string; kind: 'ean13' | 'code128'; text: string } {
  if (isValidEan13(code)) return { modules: ean13Modules(code), kind: 'ean13', text: code }
  if (isValidUpcA(code)) return { modules: ean13Modules(`0${code}`), kind: 'ean13', text: code }
  return { modules: code128Modules(code), kind: 'code128', text: code }
}

/** Crisp SVG (one rect per bar run) with 10-module quiet zones; scales to its container. */
export function barcodeSvg(code: string, opts: { height?: number; showText?: boolean } = {}): string {
  const { modules, text } = modulesFor(code)
  const height = opts.height ?? 50
  const quiet = 10
  const width = modules.length + quiet * 2
  const textH = opts.showText === false ? 0 : 11
  let bars = ''
  for (let i = 0; i < modules.length; ) {
    if (modules[i] !== '1') {
      i++
      continue
    }
    let j = i
    while (modules[j] === '1') j++
    bars += `<rect x="${quiet + i}" y="0" width="${j - i}" height="${height}"/>`
    i = j
  }
  const label = textH
    ? `<text x="${width / 2}" y="${height + 10}" text-anchor="middle" font-family="monospace" font-size="10">${text.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</text>`
    : ''
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height + textH}" shape-rendering="crispEdges">${bars}${label}</svg>`
}
