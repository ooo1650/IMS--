/**
 * USB/Bluetooth barcode scanners in keyboard mode ("HID wedge") type the code
 * and press Enter, far faster than a person. WedgeDetector tells the two apart
 * so a scan works anywhere on the POS, not only with the search box focused.
 */
import { useEffect, useRef } from 'react'

const MAX_GAP = 50 // ms between keys: scanners <30, people 100+
const MIN_LENGTH = 4

export class WedgeDetector {
  private buf = ''
  private lastAt = 0
  /** maxGap: ms between scanner keystrokes; humans are ~100ms+, scanners <30ms. */
  private maxGap: number
  private minLength: number
  constructor(maxGap = MAX_GAP, minLength = MIN_LENGTH) {
    this.maxGap = maxGap
    this.minLength = minLength
  }

  /** Feed one keydown; returns the scanned code when Enter ends a fast burst. */
  feed(key: string, at: number): string | null {
    if (at - this.lastAt > this.maxGap) this.buf = ''
    this.lastAt = at
    if (key === 'Enter') {
      const code = this.buf
      this.buf = ''
      return code.length >= this.minLength ? code : null
    }
    if (key.length === 1) this.buf += key
    return null
  }
}

/**
 * POS → Devices → Test scanner: will this scan work at the till, and if not why?
 * `times` are the keydown timestamps, `end` is the key that finished the code
 * (null = the typing just stopped).
 */
export function diagnoseScan(code: string, times: number[], end: 'Enter' | 'Tab' | null): { ok: boolean; message: string } {
  if (code.length < MIN_LENGTH)
    return { ok: false, message: `Only ${code.length} character(s) arrived. Scan again; the POS needs at least ${MIN_LENGTH} to recognise a scan.` }
  if ([...code].some((c) => c < ' ' || c > '~'))
    return { ok: false, message: 'The characters arrived changed by the keyboard layout. Switch the computer to English (US) and scan again.' }
  if (end !== 'Enter')
    return { ok: false, message: `The scanner sends ${end ?? 'nothing'} after the code, and the POS needs Enter. Scan the “Enter” (or “CR”) suffix barcode in the scanner's manual.` }
  if (times.some((t, i) => i > 0 && t - times[i - 1] > MAX_GAP))
    return { ok: false, message: 'The code arrived slowly, like typing. It works with the search box selected (F2), but not from elsewhere on the screen. Turn off any keystroke delay on the scanner.' }
  return { ok: true, message: 'The scanner works.' }
}

function isEditable(t: EventTarget | null) {
  const el = t as HTMLElement | null
  if (!el?.tagName) return false
  // Checkboxes and the like take no text: a scan made after ticking one still counts.
  if (el.tagName === 'INPUT') return !['checkbox', 'radio', 'button', 'submit', 'range', 'file'].includes((el as HTMLInputElement).type)
  return el.isContentEditable || ['TEXTAREA', 'SELECT'].includes(el.tagName)
}

/** Set a React-controlled input's value as if it had been typed. */
function setValue(el: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

/**
 * Calls onScan for scans made while no text field has focus (a focused field
 * receives the scan as typing, as before). Disabled while a modal is open.
 *
 * `inFields` (product form): a retail barcode (8–14 digits) scanned while some
 * other text box has focus is taken out of that box and passed on, so it can't
 * become a product name, a price or a pack size. Boxes marked `data-scan`
 * handle their own Enter.
 */
export function useScannerInput(onScan: (code: string) => void, enabled: boolean, inFields = false) {
  const cb = useRef(onScan)
  useEffect(() => {
    cb.current = onScan
  })
  useEffect(() => {
    if (!enabled) return
    const det = new WedgeDetector()
    let before = '' // the field's text when the current burst began
    let lastAt = -Infinity
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const el = e.target as HTMLInputElement
      const field = isEditable(el)
      if (field && !(inFields && el.tagName === 'INPUT' && !el.hasAttribute('data-scan'))) return
      if (field && e.timeStamp - lastAt > MAX_GAP) before = el.value
      lastAt = e.timeStamp
      const code = det.feed(e.key, e.timeStamp)
      if (!code) return
      if (field) {
        if (!/^\d{8,14}$/.test(code)) return
        setValue(el, before)
      }
      // Without this, the scanner's Enter presses whichever button has focus.
      e.preventDefault()
      cb.current(code)
    }
    // Capture: run before a field's own handlers change its text.
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [enabled, inFields])
}

let audio: AudioContext | null = null
/** Short confirmation tone: high = found, low = not found. */
export function beep(ok: boolean) {
  try {
    audio ??= new AudioContext()
    const o = audio.createOscillator()
    const g = audio.createGain()
    o.frequency.value = ok ? 1400 : 300
    g.gain.value = 0.08
    o.connect(g).connect(audio.destination)
    o.start()
    o.stop(audio.currentTime + (ok ? 0.08 : 0.25))
  } catch {
    /* no audio device */
  }
}
