/**
 * Hardware settings for *this till* (browser profile), not the register row on
 * the server: a USB printer paired in Chrome on one PC means nothing on another.
 * Stored in IndexedDB (kv) and mirrored in memory so print paths stay sync.
 */
import { useSyncExternalStore } from 'react'
import { kvGet, kvSet } from '../offline'

export interface SavedDevice {
  /** WebUSB: vendor/product (+ serial) to find the device again via getDevices(). */
  vendorId?: number
  productId?: number
  serialNumber?: string
  label: string
}

export interface DeviceSettings {
  printer: {
    /** register = use the register's server setting (browser dialog or HTTP endpoint). */
    connection: 'register' | 'browser' | 'usb' | 'serial'
    paper: 58 | 80
    baudRate: number
    receiptBarcode: boolean
    device?: SavedDevice
  }
  drawer: { enabled: boolean }
  display: { pole: boolean; baudRate: number; device?: SavedDevice; welcome: string }
  scanner: { anywhere: boolean; beep: boolean }
}

export const DEFAULT_DEVICE_SETTINGS: DeviceSettings = {
  printer: { connection: 'register', paper: 80, baudRate: 9600, receiptBarcode: true },
  drawer: { enabled: true },
  display: { pole: false, baudRate: 9600, welcome: 'WELCOME' },
  scanner: { anywhere: true, beep: true },
}

const KEY = 'devices'
let current: DeviceSettings = DEFAULT_DEVICE_SETTINGS
const listeners = new Set<() => void>()

function merge(saved: Partial<DeviceSettings> | undefined): DeviceSettings {
  const d = DEFAULT_DEVICE_SETTINGS
  return {
    printer: { ...d.printer, ...saved?.printer },
    drawer: { ...d.drawer, ...saved?.drawer },
    display: { ...d.display, ...saved?.display },
    scanner: { ...d.scanner, ...saved?.scanner },
  }
}

export const loaded = kvGet<Partial<DeviceSettings>>(KEY)
  .then((s) => {
    current = merge(s)
    listeners.forEach((l) => l())
  })
  .catch(() => undefined)

export function deviceSettings(): DeviceSettings {
  return current
}

export async function saveDeviceSettings(next: DeviceSettings) {
  current = next
  listeners.forEach((l) => l())
  await kvSet(KEY, next)
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => void listeners.delete(l)
}

export function useDeviceSettings(): [DeviceSettings, (next: DeviceSettings) => Promise<void>] {
  return [useSyncExternalStore(subscribe, deviceSettings), saveDeviceSettings]
}

/** Characters per line at the printer's default font. */
export function columnsFor(paper: 58 | 80) {
  return paper === 58 ? 32 : 42
}
