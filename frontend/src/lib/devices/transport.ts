/**
 * Browser-only device transports (no local install): WebUSB for USB receipt
 * printers, WebSerial for serial / USB-serial printers and pole displays.
 * Chrome/Edge only, secure context (https or localhost), and the first pairing
 * needs a click; after that the browser remembers the device for this site.
 */
import type { SavedDevice } from './settings'

// Minimal typings: lib.dom has neither API.
interface USBEndpoint { endpointNumber: number; direction: 'in' | 'out'; type: 'bulk' | 'interrupt' | 'isochronous' }
interface USBAlternate { endpoints: USBEndpoint[] }
interface USBInterface { interfaceNumber: number; claimed: boolean; alternate: USBAlternate }
interface USBDevice {
  vendorId: number; productId: number; serialNumber?: string; productName?: string; manufacturerName?: string
  opened: boolean; configuration: { interfaces: USBInterface[] } | null
  open(): Promise<void>; close(): Promise<void>; selectConfiguration(n: number): Promise<void>
  claimInterface(n: number): Promise<void>
  transferOut(endpoint: number, data: BufferSource): Promise<{ status: string }>
}
interface USB {
  getDevices(): Promise<USBDevice[]>
  requestDevice(o: { filters: object[] }): Promise<USBDevice>
}
interface SerialPort {
  readable: unknown; writable: WritableStream<Uint8Array> | null
  getInfo(): { usbVendorId?: number; usbProductId?: number }
  open(o: { baudRate: number }): Promise<void>; close(): Promise<void>
}
interface Serial {
  getPorts(): Promise<SerialPort[]>
  requestPort(o?: { filters?: object[] }): Promise<SerialPort>
}

const nav = navigator as Navigator & { usb?: USB; serial?: Serial }
export const usbSupported = () => !!nav.usb && window.isSecureContext
export const serialSupported = () => !!nav.serial && window.isSecureContext

export interface ByteSink {
  write(bytes: Uint8Array): Promise<void>
}

// --- WebUSB -------------------------------------------------------------------

const usbOpen = new Map<USBDevice, number>() // device -> bulk OUT endpoint

function describeUsb(d: USBDevice): SavedDevice {
  return {
    vendorId: d.vendorId, productId: d.productId, serialNumber: d.serialNumber,
    label: [d.manufacturerName, d.productName].filter(Boolean).join(' ') || `USB ${hex(d.vendorId)}:${hex(d.productId)}`,
  }
}
const hex = (n: number) => n.toString(16).padStart(4, '0')

/** Opens the browser's device chooser. Must run from a click handler. */
export async function pairUsb(): Promise<SavedDevice> {
  if (!usbSupported()) throw new Error('This browser has no WebUSB. Use Chrome or Edge over https (or localhost).')
  // No filter: thermal printers are often vendor-class (0xFF), not printer-class (7).
  return describeUsb(await nav.usb!.requestDevice({ filters: [] }))
}

async function findUsb(saved: SavedDevice): Promise<USBDevice> {
  const all = (await nav.usb?.getDevices()) ?? []
  const d = all.find((x) => x.vendorId === saved.vendorId && x.productId === saved.productId && (!saved.serialNumber || x.serialNumber === saved.serialNumber))
  if (!d) throw new Error(`${saved.label} is not connected (or permission was revoked). Re-pair it in Devices.`)
  return d
}

async function usbEndpoint(d: USBDevice): Promise<number> {
  const known = usbOpen.get(d)
  if (known != null && d.opened) return known
  if (!d.opened) await d.open()
  if (!d.configuration) await d.selectConfiguration(1)
  for (const itf of d.configuration!.interfaces) {
    const out = itf.alternate.endpoints.find((e) => e.direction === 'out' && e.type === 'bulk')
    if (!out) continue
    try {
      if (!itf.claimed) await d.claimInterface(itf.interfaceNumber)
    } catch (e) {
      throw new Error(
        `Couldn't claim the printer: the operating system's printer driver is using it. ` +
          `Linux: unbind usblp (see Help → Hardware). Windows: switch the device to WinUSB with Zadig. (${(e as Error).message})`,
      )
    }
    usbOpen.set(d, out.endpointNumber)
    return out.endpointNumber
  }
  throw new Error(`${d.productName ?? 'Device'} has no bulk OUT endpoint, so it isn't an ESC/POS printer`)
}

export function usbSink(saved: SavedDevice): ByteSink {
  return {
    async write(bytes) {
      const d = await findUsb(saved)
      const ep = await usbEndpoint(d)
      const r = await d.transferOut(ep, bytes as BufferSource)
      if (r.status !== 'ok') throw new Error(`Printer transfer ${r.status}`)
    },
  }
}

// --- WebSerial ----------------------------------------------------------------

const serialOpen = new Map<SerialPort, number>() // port -> baud it was opened at

function describeSerial(p: SerialPort): SavedDevice {
  const i = p.getInfo()
  return {
    vendorId: i.usbVendorId, productId: i.usbProductId,
    label: i.usbVendorId != null ? `Serial ${hex(i.usbVendorId)}:${hex(i.usbProductId ?? 0)}` : 'Serial port',
  }
}

export async function pairSerial(): Promise<SavedDevice> {
  if (!serialSupported()) throw new Error('This browser has no WebSerial. Use Chrome or Edge over https (or localhost).')
  return describeSerial(await nav.serial!.requestPort())
}

async function findSerial(saved: SavedDevice): Promise<SerialPort> {
  const ports = (await nav.serial?.getPorts()) ?? []
  const p = ports.find((x) => {
    const i = x.getInfo()
    return i.usbVendorId === saved.vendorId && i.usbProductId === saved.productId
  }) ?? (saved.vendorId == null && ports.length === 1 ? ports[0] : undefined)
  if (!p) throw new Error(`${saved.label} is not connected (or permission was revoked). Re-pair it in Devices.`)
  return p
}

export function serialSink(saved: SavedDevice, baudRate: number): ByteSink {
  return {
    async write(bytes) {
      const p = await findSerial(saved)
      if (serialOpen.get(p) !== baudRate) {
        if (serialOpen.has(p)) await p.close()
        await p.open({ baudRate })
        serialOpen.set(p, baudRate)
      }
      const w = p.writable!.getWriter()
      try {
        await w.write(bytes)
      } finally {
        w.releaseLock()
      }
    },
  }
}

/** POSTs raw bytes to an HTTP print endpoint (the register's server-side setting). */
export function httpSink(url: string): ByteSink {
  return {
    async write(bytes) {
      const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: bytes as BodyInit })
      if (!res.ok) throw new Error(`Printer responded ${res.status}`)
    },
  }
}
