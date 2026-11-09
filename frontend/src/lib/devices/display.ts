/**
 * Customer-facing display. The POS publishes what the customer should see; two
 * outputs consume it:
 *  - a second browser window (/customer-display) on a customer-facing monitor,
 *    fed over BroadcastChannel (same browser, works offline, no server round trip);
 *  - a 2×20 serial pole display, if this till paired one (WebSerial).
 */
import { padLine, poleDisplayBytes } from './escpos'
import { deviceSettings } from './settings'
import { serialSink } from './transport'

interface DisplayLine { name: string; qty: number; amount: string }
export interface DisplayState {
  mode: 'idle' | 'cart' | 'pay' | 'done'
  store: string
  lines: DisplayLine[]
  subtotal?: string
  discount?: string
  tax?: string
  total: string
  /** FonePay QR as a data: URL while waiting for the customer to pay. */
  qr?: string
  change?: string
}

const CHANNEL = 'ims-customer-display'
let channel: BroadcastChannel | null = null
let last: DisplayState = { mode: 'idle', store: '', lines: [], total: '' }
let poleTimer: ReturnType<typeof setTimeout> | null = null

function ch() {
  if (!channel && 'BroadcastChannel' in window) {
    channel = new BroadcastChannel(CHANNEL)
    // A display window that just opened asks for the current screen.
    channel.onmessage = (e) => e.data === 'hello' && channel?.postMessage(last)
  }
  return channel
}

export function publishDisplay(state: DisplayState) {
  last = state
  ch()?.postMessage(state)
  schedulePole(state)
}

/** Merge into the last published state (e.g. add the QR while paying). */
export function patchDisplay(patch: Partial<DisplayState>) {
  publishDisplay({ ...last, ...patch })
}

/** Text a 2×20 pole display shows for a state. */
export function poleLines(s: DisplayState, welcome: string, width = 20): [string, string] {
  const money = (v?: string) => (v ? `Rs ${v}` : '')
  switch (s.mode) {
    case 'cart': {
      const l = s.lines[s.lines.length - 1]
      return [l ? padLine(l.name.slice(0, width - l.amount.length - 1), l.amount, width) : '', padLine('TOTAL', money(s.total), width)]
    }
    case 'pay':
      return [padLine('TOTAL', money(s.total), width), 'PLEASE PAY']
    case 'done':
      return ['THANK YOU!', s.change ? padLine('CHANGE', money(s.change), width) : (s.store || '')]
    default:
      return [welcome, s.store]
  }
}

// Debounced: scanning fast shouldn't queue a serial write per keystroke.
function schedulePole(state: DisplayState) {
  const cfg = deviceSettings().display
  if (!cfg.pole || !cfg.device) return
  if (poleTimer) clearTimeout(poleTimer)
  poleTimer = setTimeout(() => {
    const [a, b] = poleLines(state, cfg.welcome)
    void serialSink(cfg.device!, cfg.baudRate).write(poleDisplayBytes(a, b)).catch(() => undefined)
  }, 120)
}

export async function testPole(text: [string, string]) {
  const cfg = deviceSettings().display
  if (!cfg.device) throw new Error('Pair the pole display first')
  await serialSink(cfg.device, cfg.baudRate).write(poleDisplayBytes(...text))
}

/** Used by the display window: subscribe and ask for the current state. */
export function subscribeDisplay(onState: (s: DisplayState) => void) {
  if (!('BroadcastChannel' in window)) return () => undefined
  const c = new BroadcastChannel(CHANNEL)
  c.onmessage = (e) => e.data !== 'hello' && onState(e.data as DisplayState)
  c.postMessage('hello')
  return () => c.close()
}

export function openDisplayWindow() {
  const w = window.open('/customer-display', 'ims-customer-display', 'width=1024,height=640')
  if (!w) throw new Error('Pop-up blocked: allow pop-ups for this site to open the customer display')
  return w
}
