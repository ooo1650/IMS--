/** Per-till hardware setup: receipt printer, cash drawer, customer display, scanner. */
import { useState, type ReactNode } from 'react'
import { openDisplayWindow, testPole } from '../lib/devices/display'
import { useDeviceSettings, type DeviceSettings as Settings } from '../lib/devices/settings'
import { pairSerial, pairUsb, serialSupported, usbSupported } from '../lib/devices/transport'
import { driverFor } from '../lib/printer'
import type { RegisterCfg } from '../lib/types'
import { CameraScanner } from './CameraScanner'
import { ScannerTest } from './ScannerTest'
import { TabletPairing } from './TabletPairing'
import { Icon, type IconName } from './Icon'
import { Badge, Button, ErrorBox, Field, Modal } from './ui'

const BAUDS = [9600, 19200, 38400, 57600, 115200]

function Section({ icon, title, children }: { icon: IconName; title: string; children: ReactNode }) {
  return (
    <section className="device-section">
      <h3><Icon name={icon} size={16} /> {title}</h3>
      {children}
    </section>
  )
}

export function DeviceSettings({ register, storeName, productFor, onClose }: {
  register: RegisterCfg
  storeName: string
  productFor: (code: string) => string | undefined
  onClose: () => void
}) {
  const [s, save] = useDeviceSettings()
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [camera, setCamera] = useState(false)
  const set = (patch: (d: Settings) => Settings) => void save(patch(s))
  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setError(null)
    setNote(null)
    try {
      await fn()
      if (ok) setNote(ok)
    } catch (e) {
      // Closing the browser's device chooser is not an error worth showing.
      if ((e as Error).name !== 'NotFoundError') setError((e as Error).message)
    }
  }
  const p = s.printer
  const serverPrinter = register.printer_driver !== 'browser' && register.printer_config?.url
    ? `HTTP endpoint ${register.printer_config.url}` : 'browser print dialog'

  return (
    <Modal title={`Devices · ${register.name}`} onClose={onClose} wide>
      <p className="muted small">Saved on this computer only. Pair each till's hardware on that till.</p>
      <ErrorBox error={error} />
      {note && <div className="alert alert-good">{note}</div>}
      <div className="device-grid">
        <Section icon="printer" title="Receipt printer">
          <Field label="Connection" hint="Full tax invoices and credit notes always use the print dialog (they need Devanagari).">
            <select value={p.connection} onChange={(e) => set((d) => ({ ...d, printer: { ...d.printer, connection: e.target.value as Settings['printer']['connection'], device: undefined } }))}>
              <option value="register">Register default ({serverPrinter})</option>
              <option value="browser">Browser print dialog</option>
              <option value="usb" disabled={!usbSupported()}>USB thermal printer (WebUSB){usbSupported() ? '' : ' — not available here'}</option>
              <option value="serial" disabled={!serialSupported()}>Serial / USB-serial printer (WebSerial){serialSupported() ? '' : ' — not available here'}</option>
            </select>
          </Field>
          {(p.connection === 'usb' || p.connection === 'serial') && (
            <div className="device-pair">
              {p.device ? <Badge tone="good">{p.device.label}</Badge> : <Badge tone="warn">Not paired</Badge>}
              <Button onClick={() => void run(async () => {
                const device = p.connection === 'usb' ? await pairUsb() : await pairSerial()
                set((d) => ({ ...d, printer: { ...d.printer, device } }))
              }, 'Printer paired.')}>{p.device ? 'Change' : 'Pair printer'}</Button>
            </div>
          )}
          <div className="form-grid">
            <Field label="Paper width">
              <select value={p.paper} onChange={(e) => set((d) => ({ ...d, printer: { ...d.printer, paper: Number(e.target.value) as 58 | 80 } }))}>
                <option value={80}>80 mm (42 characters)</option>
                <option value={58}>58 mm (32 characters)</option>
              </select>
            </Field>
            {p.connection === 'serial' && (
              <Field label="Baud rate">
                <select value={p.baudRate} onChange={(e) => set((d) => ({ ...d, printer: { ...d.printer, baudRate: Number(e.target.value) } }))}>
                  {BAUDS.map((b) => <option key={b}>{b}</option>)}
                </select>
              </Field>
            )}
          </div>
          <label className="check">
            <input type="checkbox" checked={p.receiptBarcode} onChange={(e) => set((d) => ({ ...d, printer: { ...d.printer, receiptBarcode: e.target.checked } }))} />
            Print the invoice number as a barcode (scan it at returns)
          </label>
          <Button onClick={() => void run(async () => {
            const drv = driverFor(register)
            if (!drv.testPage) throw new Error('No thermal printer set up for this till: test pages need USB, serial or an HTTP endpoint.')
            await drv.testPage(storeName)
          }, 'Test page sent.')}><Icon name="printer" size={16} /> Print test page</Button>
        </Section>

        <Section icon="wallet" title="Cash drawer">
          <p className="muted small">Plugged into the receipt printer's drawer (DK/RJ11) port. Opens after cash sales.</p>
          <label className="check">
            <input type="checkbox" checked={s.drawer.enabled} onChange={(e) => set((d) => ({ ...d, drawer: { enabled: e.target.checked } }))} />
            Open the drawer after cash sales
          </label>
          <Button disabled={!s.drawer.enabled} onClick={() => void run(() => driverFor(register).openDrawer(), 'Drawer pulse sent.')}>Test drawer</Button>
        </Section>

        <Section icon="grid" title="Customer display">
          <TabletPairing register={register} />
          <h4>Monitor plugged into this computer</h4>
          <p className="muted small">Open the display window and drag it to the customer-facing screen (F11 for full screen).</p>
          <Button onClick={() => void run(async () => openDisplayWindow())}><Icon name="grid" size={16} /> Open customer display</Button>
          <label className="check" style={{ marginTop: 14 }}>
            <input type="checkbox" checked={s.display.pole} disabled={!serialSupported()}
              onChange={(e) => set((d) => ({ ...d, display: { ...d.display, pole: e.target.checked } }))} />
            Pole display (2×20 VFD on a serial port){serialSupported() ? '' : ' — needs WebSerial'}
          </label>
          {s.display.pole && (
            <>
              <div className="device-pair">
                {s.display.device ? <Badge tone="good">{s.display.device.label}</Badge> : <Badge tone="warn">Not paired</Badge>}
                <Button onClick={() => void run(async () => {
                  const device = await pairSerial()
                  set((d) => ({ ...d, display: { ...d.display, device } }))
                }, 'Pole display paired.')}>{s.display.device ? 'Change' : 'Pair display'}</Button>
              </div>
              <div className="form-grid">
                <Field label="Baud rate">
                  <select value={s.display.baudRate} onChange={(e) => set((d) => ({ ...d, display: { ...d.display, baudRate: Number(e.target.value) } }))}>
                    {BAUDS.map((b) => <option key={b}>{b}</option>)}
                  </select>
                </Field>
                <Field label="Welcome text">
                  <input maxLength={20} value={s.display.welcome} onChange={(e) => set((d) => ({ ...d, display: { ...d.display, welcome: e.target.value } }))} />
                </Field>
              </div>
              <Button disabled={!s.display.device} onClick={() => void run(() => testPole([s.display.welcome, storeName]), 'Sent to the pole display.')}>Test pole display</Button>
            </>
          )}
        </Section>

        <Section icon="qr" title="Barcode scanner">
          <p className="muted small">USB and Bluetooth scanners work as a keyboard; no pairing needed. Set the scanner to send Enter after each code.</p>
          <label className="check">
            <input type="checkbox" checked={s.scanner.anywhere} onChange={(e) => set((d) => ({ ...d, scanner: { ...d.scanner, anywhere: e.target.checked } }))} />
            Accept scans anywhere on the POS screen (not only in the search box)
          </label>
          <label className="check">
            <input type="checkbox" checked={s.scanner.beep} onChange={(e) => set((d) => ({ ...d, scanner: { ...d.scanner, beep: e.target.checked } }))} />
            Beep on scan (high = found, low = unknown code)
          </label>
          <ScannerTest productFor={productFor} />
          <p className="muted small">No scanner? A webcam, phone or tablet camera can read barcodes instead.</p>
          <Button kind="ghost" onClick={() => setCamera(true)}>Test the camera instead</Button>
        </Section>
      </div>
      <div className="modal-foot"><Button kind="primary" onClick={onClose}>Done</Button></div>
      {camera && <CameraScanner onClose={() => setCamera(false)} onDetected={(code) => { setCamera(false); setNote(`Barcode read: ${code}`) }} />}
    </Modal>
  )
}
