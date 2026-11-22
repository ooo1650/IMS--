/**
 * Customer-facing screen. Opened from POS → Devices on a second monitor; it only
 * shows what the POS in this same browser publishes (BroadcastChannel), so it
 * needs no login and no server access. A separate device uses /display instead.
 */
import { useEffect, useState } from 'react'
import { CustomerScreen } from '../components/CustomerScreen'
import { subscribeDisplay, type DisplayState } from '../lib/devices/display'

export default function CustomerDisplay() {
  const [s, setS] = useState<DisplayState | null>(null)
  useEffect(() => {
    document.title = 'Customer display'
    return subscribeDisplay(setS)
  }, [])

  // Nothing heard yet: perhaps this address was opened on a separate device, where it can never work.
  return <CustomerScreen s={s} footer={!s && <p className="cd-hint">On a tablet or another device? <a href="/display">Open /display</a> instead.</p>} />
}
