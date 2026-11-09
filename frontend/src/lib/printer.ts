/**
 * Printer abstraction (design doc §13; payment.md Step 2).
 *
 * The server renders every tax document and records each print (the first is
 * the original; later prints are stamped "COPY OF ORIGINAL"). This module only
 * delivers what the server returned:
 *  - full tax invoices and credit/debit notes always print as HTML (A5, with
 *    the Devanagari title "कर बीजक") via the browser print dialog;
 *  - abbreviated invoices and offline slips go to a thermal printer as ESC/POS
 *    when this till has one: WebUSB / WebSerial (paired in POS → Devices) or
 *    the register's HTTP print endpoint;
 *  - the cash drawer pulse is an ESC/POS command on the printer's DK port.
 */
import { EscPos, drawerPulse, padLine } from './devices/escpos'
import { columnsFor, deviceSettings } from './devices/settings'
import { httpSink, serialSink, usbSink, type ByteSink } from './devices/transport'
import type { DocumentCtx, PrintResult, RegisterCfg } from './types'

const WIDTH = 42 // characters on 80mm paper

/** Plain-text layout for thermal printers (Latin only — thermal fonts lack Devanagari). */
function receiptText(d: DocumentCtx, width = WIDTH): string[] {
  const pad = (left: string, right: string) => padLine(left, right, width)
  const out: string[] = []
  const center = (s: string) => ' '.repeat(Math.max(0, Math.floor((width - s.length) / 2))) + s
  out.push(center(d.seller.name), center(d.seller.address), center(`PAN/VAT: ${d.seller.pan || '-'}`))
  out.push(center(d.title_en))
  if (d.stamp.label) out.push(center(`*** ${d.stamp.label} ***`))
  if (d.cancelled) out.push(center('*** CANCELLED ***'))
  out.push('-'.repeat(width))
  // Offline slips have no BS date (the BS calendar lives on the server).
  out.push(`No: ${d.number}`, d.issued.bs ? pad(`Date: ${d.issued.bs} BS`, d.issued.ad) : `Date: ${d.issued.ad} ${d.issued.time}`)
  if (d.transaction && d.transaction.bs !== d.issued.bs) out.push(`Transaction: ${d.transaction.bs} BS`)
  if (d.buyer.name) out.push(`Buyer: ${d.buyer.name}${d.buyer.pan ? ` PAN ${d.buyer.pan}` : ''}`)
  if (d.payment_method) out.push(pad(`Payment: ${d.payment_method}`, d.register ? `Till ${d.register}` : ''))
  out.push('-'.repeat(width))
  for (const l of d.lines) {
    out.push(`${l.sno}. ${l.description}`.slice(0, width))
    out.push(pad(`   ${l.qty} x ${l.rate ?? ''}`, l.amount))
  }
  out.push('-'.repeat(width))
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
  out.push('', ...wrap(d.in_words, width), '', center('Thank you!'))
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

interface PrinterDriver {
  print(job: { html: string; document: DocumentCtx }): Promise<void>
  openDrawer(): Promise<void>
}

/** Prints server-rendered HTML through the browser dialog (kiosk mode prints silently). */
export async function printHtml(html: string, title: string) {
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

/** ESC/POS printer behind any byte transport (WebUSB, WebSerial or an HTTP endpoint). */
class EscPosDriver implements PrinterDriver {
  sink: ByteSink
  width: number
  barcode: boolean
  drawerEnabled: boolean
  constructor(sink: ByteSink, width: number, barcode: boolean, drawerEnabled: boolean) {
    this.sink = sink
    this.width = width
    this.barcode = barcode
    this.drawerEnabled = drawerEnabled
  }

  async print({ html, document }: { html: string; document: DocumentCtx }) {
    // Full invoices and notes need Devanagari and the Rule 17 layout: browser print.
    if (document.kind !== 'abbreviated' && document.kind !== 'provisional') return printHtml(html, document.number)
    const job = new EscPos()
    for (const l of receiptText(document, this.width)) job.line(l)
    if (this.barcode && document.number) job.line().barcode128(document.number)
    job.cut()
    // The drawer pulse rides on the same job so it opens as the receipt cuts.
    if (document.open_drawer && this.drawerEnabled) job.drawer()
    await this.sink.write(job.build())
    printedDrawer.add(document)
  }

  async openDrawer() {
    if (this.drawerEnabled) await this.sink.write(drawerPulse())
  }

  async testPage(storeName: string) {
    const job = new EscPos().align('center').bold(true).line(storeName).bold(false).line('Printer test').align('left')
    job.line('-'.repeat(this.width)).line(padLine('Paper width', `${this.width} chars`, this.width))
    job.line(padLine('Time', new Date().toLocaleString(), this.width)).line('0123456789'.repeat(Math.ceil(this.width / 10)).slice(0, this.width))
    if (this.barcode) job.line().barcode128('TEST-12345')
    await this.sink.write(job.cut().build())
  }
}

/** Documents whose drawer pulse already went out with the receipt bytes. */
const printedDrawer = new WeakSet<DocumentCtx>()

function sinkFor(register: RegisterCfg | undefined): ByteSink | null {
  const s = deviceSettings().printer
  if (s.connection === 'usb' && s.device) return usbSink(s.device)
  if (s.connection === 'serial' && s.device) return serialSink(s.device, s.baudRate)
  if (s.connection === 'register' && register && register.printer_driver !== 'browser' && register.printer_config?.url)
    return httpSink(register.printer_config.url)
  return null
}

export function driverFor(register: RegisterCfg | undefined): PrinterDriver & { testPage?: (store: string) => Promise<void> } {
  const s = deviceSettings()
  const sink = sinkFor(register)
  if (!sink) return new BrowserPrintDriver()
  return new EscPosDriver(sink, columnsFor(s.printer.paper), s.printer.receiptBarcode, s.drawer.enabled)
}

/** Print a document and open the drawer once (with the receipt when it went to ESC/POS). */
export async function printDocument(register: RegisterCfg | undefined, job: { html: string; document: DocumentCtx }) {
  const driver = driverFor(register)
  await driver.print(job)
  // Full invoices print via the dialog, so the drawer still needs its own pulse.
  if (job.document.open_drawer && !printedDrawer.has(job.document)) await driver.openDrawer()
}

export const printResult = (register: RegisterCfg | undefined, result: PrintResult) => printDocument(register, result)

/** Offline (per-branch series): NOT a tax invoice — the invoice is issued on sync. */
export function provisionalSlipHtml(d: DocumentCtx): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  return `<!doctype html><meta charset="utf-8"><title>${esc(d.number)}</title>
    <style>body{font:12px/1.35 ui-monospace,monospace;margin:8px;white-space:pre}@page{size:80mm auto;margin:0}</style>
    <body>${receiptText(d).map(esc).join('\n')}</body>`
}
