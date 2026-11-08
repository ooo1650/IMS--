/** ESC/POS byte builder for receipt printers and 2×20 pole displays. */

const ESC = 0x1b
const GS = 0x1d

const REPLACE: Record<string, string> = {
  '—': '-', '–': '-', '‘': "'", '’': "'", '“': '"', '”': '"', '…': '...', '•': '*', '×': 'x', '₹': 'Rs', ' ': ' ',
}

/** Thermal fonts are single-byte code pages: keep printable ASCII, transliterate the common rest. */
export function toPrinterText(s: string): string {
  return [...s.normalize('NFKD')]
    .map((ch) => REPLACE[ch] ?? ch)
    .join('')
    .replace(/[̀-ͯ]/g, '') // accents left over from NFKD
    .replace(/[^\x20-\x7e\n]/g, '?')
}

export class EscPos {
  private bytes: number[] = [ESC, 0x40] // initialize

  raw(...b: number[]) {
    this.bytes.push(...b)
    return this
  }

  text(s: string) {
    for (const ch of toPrinterText(s)) this.bytes.push(ch.charCodeAt(0))
    return this
  }

  line(s = '') {
    return this.text(s).raw(0x0a)
  }

  align(a: 'left' | 'center' | 'right') {
    return this.raw(ESC, 0x61, a === 'left' ? 0 : a === 'center' ? 1 : 2)
  }

  bold(on: boolean) {
    return this.raw(ESC, 0x45, on ? 1 : 0)
  }

  /** CODE128 (set B) with human-readable text below; for scanning invoice numbers at returns. */
  barcode128(data: string) {
    const d = toPrinterText(data)
    if (!d || d.length > 60) return this
    const payload = [0x7b, 0x42, ...[...d].map((c) => c.charCodeAt(0))] // "{B" + data
    return this.align('center')
      .raw(GS, 0x68, 60) // height (dots)
      .raw(GS, 0x77, 2) // module width
      .raw(GS, 0x48, 2) // HRI below
      .raw(GS, 0x6b, 73, payload.length, ...payload)
      .raw(0x0a)
      .align('left')
  }

  cut() {
    return this.raw(0x0a, 0x0a, 0x0a, GS, 0x56, 0x42, 0x00) // feed + partial cut
  }

  /** Pulse pin 2 (DK port), 50ms on / 500ms off. */
  drawer() {
    return this.raw(ESC, 0x70, 0x00, 0x19, 0xfa)
  }

  build() {
    return new Uint8Array(this.bytes)
  }
}

export const drawerPulse = () => new Uint8Array([ESC, 0x70, 0x00, 0x19, 0xfa])

/**
 * Two lines for a 2×20 VFD pole display (Epson-compatible command set):
 * clear (0x0C), then each line padded to the width so text wraps to line 2.
 */
export function poleDisplayBytes(line1: string, line2: string, width = 20): Uint8Array {
  const fit = (s: string) => toPrinterText(s).slice(0, width).padEnd(width, ' ')
  const out = [ESC, 0x40, 0x0c, ...[...(fit(line1) + fit(line2))].map((c) => c.charCodeAt(0))]
  return new Uint8Array(out)
}

/** "left ........ right" on one line. */
export function padLine(left: string, right: string, width: number) {
  const space = Math.max(1, width - left.length - right.length)
  return left + ' '.repeat(space) + right
}
