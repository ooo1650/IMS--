/** POS → Devices: check a plugged-in (keyboard-mode) barcode scanner. No camera involved. */
import { useEffect, useRef, useState } from 'react'
import { diagnoseScan } from '../lib/devices/scanner'
import { Button, Field } from './ui'

/** No key for this long after some input = the scanner sent no suffix. */
const IDLE_MS = 700
/** No input at all for this long = probably not connected. */
const SILENT_MS = 10_000

interface Result { code: string; ok: boolean; message: string; product?: string }

export function ScannerTest({ productFor }: { productFor: (code: string) => string | undefined }) {
  const [testing, setTesting] = useState(false)
  const [silent, setSilent] = useState(false)
  const [result, setResult] = useState<Result | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const times = useRef<number[]>([])
  const idle = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => () => clearTimeout(idle.current), [])
  useEffect(() => {
    if (!testing || result) return
    const t = setTimeout(() => setSilent(true), SILENT_MS)
    return () => clearTimeout(t)
  }, [testing, result])

  const finish = (end: 'Enter' | 'Tab' | null) => {
    clearTimeout(idle.current)
    const code = input.current?.value.trim() ?? ''
    if (code) {
      setSilent(false)
      setResult({ code, product: productFor(code), ...diagnoseScan(code, times.current, end) })
    }
    if (input.current) input.current.value = ''
    times.current = []
  }

  if (!testing) return <Button kind="primary" onClick={() => setTesting(true)}>Test USB or Bluetooth scanner</Button>

  return (
    <>
      <Field label="Scan any barcode now" hint="Keep this box selected. The camera is not used.">
        <input ref={input} autoFocus autoComplete="off" spellCheck={false}
          // A test scan is not an unsaved edit: don't arm the dialog's "Close without saving?" guard.
          onInput={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            clearTimeout(idle.current)
            if (e.key === 'Enter' || (e.key === 'Tab' && e.currentTarget.value)) {
              e.preventDefault()
              times.current.push(e.timeStamp)
              finish(e.key)
            } else if (e.key !== 'Tab') {
              times.current.push(e.timeStamp)
              idle.current = setTimeout(() => finish(null), IDLE_MS)
            }
          }} />
      </Field>
      {result && (
        <div className={`alert ${result.ok ? 'alert-good' : 'alert-warn'} small`} role="status">
          Received <span className="fig">{result.code}</span>. {result.message}
          {result.ok && (result.product
            ? ` It is ${result.product}.`
            : ' No product has this code yet: add it in Products → Barcodes.')}
        </div>
      )}
      {silent && !result && (
        <div className="alert alert-warn small" role="status">
          Nothing received. Check that the cable is firm at both ends (most scanners beep when connected), try another
          USB port, and click the box before scanning. If it beeps but nothing appears, set it to “USB keyboard” mode
          with the barcode in its manual.
        </div>
      )}
    </>
  )
}
