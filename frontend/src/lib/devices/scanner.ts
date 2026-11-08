/**
 * USB/Bluetooth barcode scanners in keyboard mode ("HID wedge") type the code
 * and press Enter, far faster than a person. WedgeDetector tells the two apart
 * so a scan works anywhere on the POS, not only with the search box focused.
 */
import { useEffect, useRef } from 'react'

export class WedgeDetector {
  private buf = ''
  private lastAt = 0
  /** maxGap: ms between scanner keystrokes; humans are ~100ms+, scanners <30ms. */
  private maxGap: number
  private minLength: number
  constructor(maxGap = 50, minLength = 4) {
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

function isEditable(t: EventTarget | null) {
  const el = t as HTMLElement | null
  if (!el?.tagName) return false
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
}

/**
 * Calls onScan for scans made while no text field has focus (a focused field
 * receives the scan as typing, as before). Disabled while a modal is open.
 */
export function useScannerInput(onScan: (code: string) => void, enabled: boolean) {
  const cb = useRef(onScan)
  useEffect(() => {
    cb.current = onScan
  })
  useEffect(() => {
    if (!enabled) return
    const det = new WedgeDetector()
    const onKey = (e: KeyboardEvent) => {
      if (isEditable(e.target) || e.ctrlKey || e.metaKey || e.altKey) return
      const code = det.feed(e.key, e.timeStamp)
      if (code) {
        e.preventDefault()
        cb.current(code)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
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
