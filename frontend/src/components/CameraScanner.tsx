/** Scan a barcode with the device camera (phones, tablets, laptop webcams). */
import { useEffect, useRef, useState } from 'react'
import { ErrorBox, Modal } from './ui'

interface Detected { rawValue: string }
interface BarcodeDetectorLike { detect(src: HTMLVideoElement): Promise<Detected[]> }
type DetectorCtor = (new (o?: { formats?: string[] }) => BarcodeDetectorLike) & { getSupportedFormats?: () => Promise<string[]> }

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'qr_code']

export function CameraScanner({ title = 'Scan barcode', onDetected, onClose }: {
  title?: string
  onDetected: (code: string) => void
  onClose: () => void
}) {
  const video = useRef<HTMLVideoElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [engine, setEngine] = useState('')
  const done = useRef(false)
  const found = useRef(onDetected)
  useEffect(() => {
    found.current = onDetected
  })

  useEffect(() => {
    let stopped = false
    let stream: MediaStream | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    let zxingStop: (() => void) | null = null
    const hit = (code: string) => {
      if (done.current || stopped) return
      done.current = true
      found.current(code)
    }

    ;(async () => {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
        throw new Error('The camera needs https (or localhost). Open the app over its https address.')
      const constraints: MediaStreamConstraints = { video: { facingMode: { ideal: 'environment' } }, audio: false }
      const Native = (window as unknown as { BarcodeDetector?: DetectorCtor }).BarcodeDetector
      const formats = Native ? ((await Native.getSupportedFormats?.()) ?? []) : []
      if (Native && formats.some((f) => FORMATS.includes(f))) {
        setEngine('built-in detector')
        stream = await navigator.mediaDevices.getUserMedia(constraints)
        if (stopped) return
        video.current!.srcObject = stream
        await video.current!.play()
        const det = new Native({ formats: FORMATS.filter((f) => formats.includes(f)) })
        const tick = async () => {
          if (stopped) return
          try {
            const [first] = await det.detect(video.current!)
            if (first?.rawValue) return hit(first.rawValue)
          } catch {
            /* frame not ready */
          }
          timer = setTimeout(tick, 150)
        }
        void tick()
      } else {
        setEngine('ZXing')
        const { BrowserMultiFormatReader } = await import('@zxing/browser')
        if (stopped) return
        const controls = await new BrowserMultiFormatReader().decodeFromConstraints(constraints, video.current!, (result) => {
          if (result) hit(result.getText())
        })
        zxingStop = () => controls.stop()
        if (stopped) zxingStop()
      }
    })().catch((e: Error) => {
      if (stopped) return
      setError(e.name === 'NotAllowedError' ? 'Camera permission was denied. Allow the camera for this site and try again.'
        : e.name === 'NotFoundError' ? 'No camera found on this device.' : e.message)
    })

    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      zxingStop?.()
      stream?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  return (
    <Modal title={title} onClose={onClose}>
      <ErrorBox error={error} />
      <div className="camera-box">
        <video ref={video} muted playsInline />
        <span className="camera-frame" aria-hidden />
      </div>
      <p className="muted small center-text">Hold the barcode inside the frame.{engine && ` · ${engine}`}</p>
    </Modal>
  )
}
