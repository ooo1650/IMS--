/**
 * Printer abstraction (design doc §13; payment.md Step 2).
 *
 * The server renders every tax document and records each print (the first is
 * the original; later prints are stamped "COPY OF ORIGINAL"). This module only
 * delivers what the server returned:
 *  - full tax invoices and credit/debit notes always print as HTML (A5, with
 *    the Devanagari title "कर बीजक") via the browser print dialog;
 *  - abbreviated invoices can go to a thermal printer as ESC/POS text, if the
 *    register has a network/USB bridge configured;
 *  - the cash drawer pulse is an ESC/POS command on the printer's DK port.
 */
import type { DocumentCtx, PrintResult, RegisterCfg } from './types'

const WIDTH = 42 // characters on 80mm paper

function pad(left: string, right: string, width = WIDTH) {
  const space = Math.max(1, width - left.length - right.length)
  return left + ' '.repeat(space) + right
}

/** Plain-text layout for thermal printers (Latin only — thermal fonts lack Devanagari). */
export function receiptText(d: DocumentCtx): string[] {
  const out: string[] = []
  const center = (s: string) => ' '.repeat(Math.max(0, Math.floor((WIDTH - s.length) / 2))) + s
  out.push(center(d.seller.name), center(d.seller.address), center(`PAN/VAT: ${d.seller.pan || '-'}`))
  out.push(center(d.title_en))
  if (d.stamp.label) out.push(center(`*** ${d.stamp.label} ***`))
  if (d.cancelled) out.push(center('*** CANCELLED ***'))
  out.push('-'.repeat(WIDTH))
  // Offline slips have no BS date (the BS calendar lives on the server).
  out.push(`No: ${d.number}`, d.issued.bs ? pad(`Date: ${d.issued.bs} BS`, d.issued.ad) : `Date: ${d.issued.ad} ${d.issued.time}`)
  if (d.transaction && d.transaction.bs !== d.issued.bs) out.push(`Transaction: ${d.transaction.bs} BS`)
  if (d.buyer.name) out.push(`Buyer: ${d.buyer.name}${d.buyer.pan ? ` PAN ${d.buyer.pan}` : ''}`)
  if (d.payment_method) out.push(pad(`Payment: ${d.payment_method}`, d.register ? `Till ${d.register}` : ''))
  out.push('-'.repeat(WIDTH))
  for (const l of d.lines) {
    out.push(`${l.sno}. ${l.description}`.slice(0, WIDTH))
    out.push(pad(`   ${l.qty} x ${l.rate ?? ''}`, l.amount))
  }
  out.push('-'.repeat(WIDTH))
  if (d.subtotal) out.push(pad('Total', d.subtotal))
  if (d.discount && d.discount !== '0.00') out.push(pad('Discount', `-${d.discount}`))
  out.push(pad('Taxable value', d.taxable_value), pad(d.vat_label ?? 'VAT', d.vat))
  for (const [k, label] of [['excise', 'Excise'], ['hst', 'HST'], ['esf', 'ESF'], ['round_adjust', 'Rounding']] as const) {
    if (d[k]) out.push(pad(label, String(d[k])))
  }
  if (d.digital_discount) out.push(pad('Digital VAT discount', `-${d.digital_discount}`))
  out.push(pad('GRAND TOTAL', `Rs ${d.total}`))
  if (d.tendered) out.push(pad('Tendered', d.tendered))
  if (d.change) out.push(pad('Change', d.change))
  out.push('', ...wrap(d.in_words), '', center('Thank you!'))
  return out
}

function wrap(text: string, width = WIDTH): string[] {
  const lines: string[] = []
  let cur = ''
  for (const word of text.split(' ')) {
    if ((cur + ' ' + word).trim().length > width) {
      lines.push(cur)
      cur = word
    } else cur = (cur + ' ' + word).trim()
  }
  if (cur) lines.push(cur)
  return lines
}

export interface PrinterDriver {
  print(job: { html: string; document: DocumentCtx }): Promise<void>
  openDrawer(): Promise<void>
}

/** Prints server-rendered HTML through the browser dialog (kiosk mode prints silently). */
async function printHtml(html: string, title: string) {
  const w = window.open('', 'ims-print', 'width=520,height=720')
  if (!w) throw new Error('Pop-up blocked: allow pop-ups for this site to print invoices')
  w.document.write(html)
  w.document.title = title
  w.document.close()
  w.focus()
  w.print()
  setTimeout(() => w.close(), 500)
}

class BrowserPrintDriver implements PrinterDriver {
  async print({ html, document }: { html: string; document: DocumentCtx }) {
    await printHtml(html, `${document.title_en} ${document.number}`)
  }
  async openDrawer() {
    // Browser printing cannot pulse the drawer; drawers need ESC/POS.
  }
}

const ESC = 0x1b
const GS = 0x1d

function escposBytes(lines: string[], drawer: boolean): Uint8Array {
  const enc = new TextEncoder()
  const parts: number[] = [ESC, 0x40] // init
  for (const l of lines) parts.push(...enc.encode(l + '\n'))
  parts.push(0x0a, 0x0a, 0x0a, GS, 0x56, 0x42, 0x00) // feed + partial cut
  if (drawer) parts.push(ESC, 0x70, 0x00, 0x19, 0xfa) // pulse pin 2 (DK port)
  return new Uint8Array(parts)
}

class HttpEscPosDriver implements PrinterDriver {
  url: string
  constructor(url: string) {
    this.url = url
  }
  private async send(bytes: Uint8Array) {
    const res = await fetch(this.url, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: bytes as BodyInit })
    if (!res.ok) throw new Error(`Printer responded ${res.status}`)
  }
  async print({ html, document }: { html: string; document: DocumentCtx }) {
    // Full invoices and notes need Devanagari and the Rule 17 layout: browser print.
    if (document.kind !== 'abbreviated' && document.kind !== 'provisional') return printHtml(html, document.number)
    await this.send(escposBytes(receiptText(document), !!document.open_drawer))
  }
  async openDrawer() {
    await this.send(new Uint8Array([ESC, 0x70, 0x00, 0x19, 0xfa]))
  }
}

export function driverFor(register: RegisterCfg | undefined): PrinterDriver {
  if (register && register.printer_driver !== 'browser' && register.printer_config?.url)
    return new HttpEscPosDriver(register.printer_config.url)
  return new BrowserPrintDriver()
}

export async function printResult(register: RegisterCfg | undefined, result: PrintResult) {
  const driver = driverFor(register)
  await driver.print(result)
  if (result.document.open_drawer) await driver.openDrawer()
}

/** Offline (per-branch series): NOT a tax invoice — the invoice is issued on sync. */
export function provisionalSlipHtml(d: DocumentCtx): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  return `<!doctype html><meta charset="utf-8"><title>${esc(d.number)}</title>
    <style>body{font:12px/1.35 ui-monospace,monospace;margin:8px;white-space:pre}@page{size:80mm auto;margin:0}</style>
    <body>${receiptText(d).map(esc).join('\n')}</body>`
}
