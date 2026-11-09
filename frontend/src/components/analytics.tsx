/**
 * Report charts (dataviz method: thin marks, recessive grid, legend for 2+
 * series, hover + keyboard readout, a table view for every chart).
 *
 * Colours: single-measure charts use the brand series colour (--series-1).
 * Entities with identity (payment methods) use the validated categorical
 * palette in fixed slot order (--cat-1..5), so a method keeps its colour
 * whatever is filtered. Magnitude (heatmap) uses one blue ramp, light → dark.
 */
import { type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import { npr } from '../lib/money'

// --- formatting ---------------------------------------------------------------

/** Compact rupees the Nepali way: Rs 950 · Rs 12.5K · Rs 3.4L (lakh) · Rs 1.2Cr (crore). */
export function nprCompact(paisa: number): string {
  const r = Math.round(paisa / 100)
  const a = Math.abs(r)
  const fmt = (v: number, unit: string) => `${v >= 100 ? Math.round(v) : Number(v.toFixed(1))}${unit}`
  const body = a >= 1e7 ? fmt(a / 1e7, 'Cr') : a >= 1e5 ? fmt(a / 1e5, 'L') : a >= 1e3 ? fmt(a / 1e3, 'K') : String(a)
  return `${r < 0 ? '−' : ''}Rs ${body}`
}

export const count = (n: number) => n.toLocaleString('en-IN')
export const pct = (bp: number | null | undefined) => (bp == null ? '—' : `${(bp / 100).toFixed(1)}%`)

const dayFmt = new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric' })
const longDayFmt = new Intl.DateTimeFormat('en', { weekday: 'short', month: 'short', day: 'numeric' })
const shortDate = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00`))
export const longDate = (iso: string) => longDayFmt.format(new Date(`${iso}T00:00:00`))

/** Clean axis: 0 and up to 4 round steps covering ``max``. */
function niceTicks(max: number): number[] {
  if (max <= 0) return [0, 1]
  const raw = max / 4
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag
  const top = Math.ceil(max / step) * step
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step)
}

function useWidth(min = 240) {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(640)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.floor(e.contentRect.width))))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [min])
  return [ref, w] as const
}

// --- stat tile with delta + sparkline -------------------------------------------

/** Change vs the previous period. ``upIsGood`` decides the colour; the sign and arrow carry it too. */
export function Delta({ now, before, upIsGood = true, label }: { now: number; before: number; upIsGood?: boolean; label: string }) {
  if (!before) {
    return <span className="delta delta-flat" title={`Nothing in the ${label}`}>{now ? 'no earlier sales to compare' : 'no change'}</span>
  }
  const change = (now - before) / Math.abs(before)
  const flat = Math.abs(change) < 0.005
  const good = flat ? null : (change > 0) === upIsGood
  const cls = flat ? 'delta-flat' : good ? 'delta-good' : 'delta-bad'
  const arrow = flat ? '→' : change > 0 ? '▲' : '▼'
  // Past +1000% a percentage stops meaning anything to a reader: say "12×" instead.
  const text = change >= 10
    ? `${Math.round(now / before).toLocaleString('en-IN')}×`
    : `${change > 0 ? '+' : flat ? '' : '−'}${Math.abs(change * 100).toFixed(Math.abs(change) < 0.1 ? 1 : 0)}%`
  return (
    <span className={`delta ${cls}`} title={`Previous: ${label}`}>
      <span aria-hidden>{arrow}</span> {flat ? '0%' : text} <span className="muted">vs {label}</span>
    </span>
  )
}

/** A stat card is a number: its label, the figure and its change (no sparkline). */
export function StatTile({ label, value, delta, sub }: {
  label: string; value: string; delta?: ReactNode; sub?: string
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {delta && <div className="stat-delta">{delta}</div>}
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

// --- trend line: this period vs the previous one ----------------------------------

interface TrendPoint { date: string; value: number; previous?: number; extra?: [string, string][] }

export function TrendChart({ points, format, axisFormat, seriesLabel, previousLabel, title }: {
  points: TrendPoint[]; format: (v: number) => string; axisFormat: (v: number) => string
  seriesLabel: string; previousLabel?: string; title: string
}) {
  const [wrap, W] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const n = points.length
  const H = 320
  const pad = { l: 72, r: 16, t: 16, b: 32 }
  const showPrev = previousLabel != null && points.some((p) => p.previous)
  const max = Math.max(1, ...points.map((p) => Math.max(p.value, showPrev ? p.previous ?? 0 : 0)))
  const ticks = niceTicks(max)
  const top = ticks[ticks.length - 1]
  const x = (i: number) => pad.l + (n < 2 ? (W - pad.l - pad.r) / 2 : (i * (W - pad.l - pad.r)) / (n - 1))
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / top)
  const base = H - pad.b
  const line = (vals: number[]) => vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
  const cur = line(points.map((p) => p.value))
  const area = n ? `${cur}L${x(n - 1).toFixed(1)},${base}L${x(0).toFixed(1)},${base}Z` : ''
  const prev = showPrev ? line(points.map((p) => p.previous ?? 0)) : ''
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - pad.l) / 72))))

  const pick = (clientX: number, rect: DOMRect) => {
    const px = clientX - rect.left
    const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1))
    setHover(Math.max(0, Math.min(n - 1, i)))
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowRight') setHover((h) => Math.min(n - 1, (h ?? -1) + 1))
    else if (e.key === 'ArrowLeft') setHover((h) => Math.max(0, (h ?? n) - 1))
    else if (e.key === 'Escape') setHover(null)
    else return
    e.preventDefault()
  }
  const hp = hover != null ? points[hover] : null
  const tipLeft = hover != null ? x(hover) : 0
  const flip = tipLeft > W * 0.62

  return (
    <figure className="chart">
      {showPrev && (
        <div className="legend" aria-label="Legend">
          <span><i className="key-line key-current" /> {seriesLabel}</span>
          <span><i className="key-line key-previous" /> {previousLabel}</span>
        </div>
      )}
      <div className="chart-wrap" ref={wrap}>
        <svg width={W} height={H} role="img" aria-label={title}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className={t ? 'grid' : 'axis'} />
              <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" className="tick">{axisFormat(t)}</text>
            </g>
          ))}
          {points.map((p, i) => (i % every === 0 || i === n - 1) && (n - 1 - i >= every || i === n - 1) ? (
            <text key={p.date} x={x(i)} y={H - 8} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'} className="tick">{shortDate(p.date)}</text>
          ) : null)}
          {showPrev && <path d={prev} className="line-previous" />}
          <path d={area} className="area" />
          <path d={cur} className="line-current" />
          {n > 0 && hover == null && <circle cx={x(n - 1)} cy={y(points[n - 1].value)} r={4} className="dot-current" />}
          {hp && (
            <g>
              <line x1={x(hover!)} x2={x(hover!)} y1={pad.t} y2={base} className="crosshair" />
              {showPrev && <circle cx={x(hover!)} cy={y(hp.previous ?? 0)} r={4} className="dot-previous" />}
              <circle cx={x(hover!)} cy={y(hp.value)} r={4.5} className="dot-current" />
            </g>
          )}
          <rect x={pad.l} y={0} width={Math.max(0, W - pad.l - pad.r)} height={H} fill="transparent" tabIndex={0}
            className="hit" aria-label={`${title}. Use the arrow keys to read each day.`}
            onPointerMove={(e) => pick(e.clientX, e.currentTarget.ownerSVGElement!.getBoundingClientRect())}
            onPointerLeave={() => setHover(null)} onFocus={() => setHover(n - 1)} onBlur={() => setHover(null)} onKeyDown={onKey} />
        </svg>
        {hp && (
          <div className="tooltip chart-tip" style={{ left: tipLeft, transform: flip ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)' }}>
            <div className="muted small">{longDate(hp.date)}</div>
            <div className="tip-row"><i className="key-line key-current" /><strong>{format(hp.value)}</strong><span className="muted">{seriesLabel}</span></div>
            {showPrev && <div className="tip-row"><i className="key-line key-previous" /><strong>{format(hp.previous ?? 0)}</strong><span className="muted">{previousLabel}</span></div>}
            {hp.extra?.map(([k, v]) => <div key={k} className="tip-row tip-extra"><strong>{v}</strong><span className="muted">{k}</span></div>)}
          </div>
        )}
      </div>
    </figure>
  )
}

// --- heatmap: weekday × hour -------------------------------------------------------

export interface HeatCell { weekday: number; hour: number; transactions: number; revenue_paisa: number }
const WEEK = [7, 1, 2, 3, 4, 5, 6] // Nepal's week starts on Sunday (ISO 7)
const WEEKDAY = { 1: 'Mon', 2: 'Tue', 3: 'Wed', 4: 'Thu', 5: 'Fri', 6: 'Sat', 7: 'Sun' } as Record<number, string>
const RAMP = 7 // --heat-1..7: one blue ramp, light → dark
const hourLabel = (h: number) => `${h % 12 || 12}${h < 12 ? 'am' : 'pm'}`

export function Heatmap({ cells }: { cells: HeatCell[] }) {
  const [wrap, W] = useWidth(280)
  const [hover, setHover] = useState<HeatCell | null>(null)
  const byKey = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c]))
  const used = cells.filter((c) => c.transactions).map((c) => c.hour)
  let lo = used.length ? Math.min(...used) : 8
  let hi = used.length ? Math.max(...used) : 20
  while (hi - lo < 9) { if (lo > 0) lo--; if (hi - lo < 9 && hi < 23) hi++ }
  const hours = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  const max = Math.max(1, ...cells.map((c) => c.transactions))
  const labelW = 40, gap = 2, cellH = 26
  const cellW = Math.max(14, (W - labelW) / hours.length)
  const step = (v: number) => (v ? Math.min(RAMP, 1 + Math.floor((v / max) * RAMP * 0.999)) : 0)
  const H = WEEK.length * cellH + 22
  const labelEvery = cellW < 30 ? 3 : cellW < 44 ? 2 : 1
  return (
    <figure className="chart">
      <div className="chart-wrap" ref={wrap}>
        <svg width={W} height={H} role="img" aria-label="Number of sales by weekday and hour">
          {WEEK.map((wd, r) => (
            <g key={wd}>
              <text x={0} y={r * cellH + cellH / 2 + 4} className="tick">{WEEKDAY[wd]}</text>
              {hours.map((h, c) => {
                const cell = byKey.get(`${wd}-${h}`) ?? { weekday: wd, hour: h, transactions: 0, revenue_paisa: 0 }
                const on = hover?.weekday === wd && hover?.hour === h
                return (
                  <rect key={h} x={labelW + c * cellW} y={r * cellH} width={cellW - gap} height={cellH - gap} rx={3}
                    className={`heat heat-${step(cell.transactions)} ${on ? 'heat-on' : ''}`} tabIndex={0}
                    aria-label={`${WEEKDAY[wd]} ${hourLabel(h)}: ${cell.transactions} sales, ${npr(cell.revenue_paisa)}`}
                    onPointerEnter={() => setHover(cell)} onPointerLeave={() => setHover(null)}
                    onFocus={() => setHover(cell)} onBlur={() => setHover(null)} />
                )
              })}
            </g>
          ))}
          {hours.map((h, c) => c % labelEvery === 0 && (
            <text key={h} x={labelW + c * cellW + (cellW - gap) / 2} y={H - 6} textAnchor="middle" className="tick">{hourLabel(h)}</text>
          ))}
        </svg>
        {hover && (
          <div className="tooltip chart-tip" style={{
            left: labelW + hours.indexOf(hover.hour) * cellW + cellW / 2,
            top: WEEK.indexOf(hover.weekday) * cellH + cellH + 4, transform: 'translateX(-50%)',
          }}>
            <div className="muted small">{WEEKDAY[hover.weekday]} · {hourLabel(hover.hour)}–{hourLabel((hover.hour + 1) % 24)}</div>
            <div className="tip-row"><strong>{count(hover.transactions)}</strong><span className="muted">sales</span></div>
            <div className="tip-row"><strong>{npr(hover.revenue_paisa)}</strong><span className="muted">revenue</span></div>
          </div>
        )}
      </div>
      <div className="heat-scale" aria-hidden>
        <span className="muted small">Fewer sales</span>
        {Array.from({ length: RAMP }, (_, i) => <i key={i} className={`heat-${i + 1}`} />)}
        <span className="muted small">More (busiest hour: {count(max)} sales)</span>
      </div>
    </figure>
  )
}

/** Table twin of the heatmap: per weekday totals and its busiest hour. */
export function heatmapRows(cells: HeatCell[]) {
  return WEEK.map((wd) => {
    const day = cells.filter((c) => c.weekday === wd)
    const busiest = day.reduce<HeatCell | null>((a, c) => (!a || c.transactions > a.transactions ? c : a), null)
    return {
      day: WEEKDAY[wd], transactions: day.reduce((a, c) => a + c.transactions, 0),
      revenue: day.reduce((a, c) => a + c.revenue_paisa, 0),
      busiest: busiest && busiest.transactions ? `${hourLabel(busiest.hour)}–${hourLabel((busiest.hour + 1) % 24)}` : '—',
    }
  })
}

// --- ranked horizontal bars -----------------------------------------------------------

interface RankedRow { key: string; label: string; sub?: string; value: number; display: string; note?: ReactNode }

export function RankedBars({ rows, label }: { rows: RankedRow[]; label: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value))
  if (!rows.length) return <div className="empty">No sales in this period</div>
  return (
    <div className="ranked" role="list" aria-label={label}>
      {rows.map((r) => (
        <div key={r.key} className="ranked-row" role="listitem" tabIndex={0}
          aria-label={`${r.label}: ${r.display}${r.sub ? `, ${r.sub}` : ''}`}>
          <div className="ranked-label">
            <span className="cell-strong">{r.label}</span>
            {r.sub && <span className="muted small">{r.sub}</span>}
          </div>
          <div className="ranked-track">
            <span className="ranked-bar" style={{ width: `${r.value > 0 ? Math.max(1.5, (r.value / max) * 100) : 0}%` }} />
            <span className="ranked-value">{r.display}</span>
          </div>
          <div className="ranked-note">{r.note}</div>
        </div>
      ))}
    </div>
  )
}

// --- tall segmented bar (part of a whole) -------------------------------------------------

export interface Segment { key: string; label: string; value: number; slot: number }

export function SegmentedBar({ parts, format }: { parts: Segment[]; format: (v: number) => string }) {
  const [wrap, W] = useWidth(200)
  const [hover, setHover] = useState<string | null>(null)
  const total = parts.reduce((a, p) => a + p.value, 0)
  if (!total) return <div className="empty">No payments in this period</div>
  const shown = parts.filter((p) => p.value > 0)
  const gaps = (shown.length - 1) * 2
  const hp = shown.find((p) => p.key === hover)
  return (
    <figure className="chart">
      <div className="chart-wrap" ref={wrap}>
        <div className="segbar" role="img" aria-label={shown.map((p) => `${p.label} ${Math.round((p.value / total) * 100)}%`).join(', ')}>
          {shown.map((p) => {
            const share = p.value / total
            const px = share * (W - gaps)
            return (
              <div key={p.key} className={`seg cat-${p.slot} ${hover === p.key ? 'seg-on' : ''}`} style={{ width: px }}
                tabIndex={0} aria-label={`${p.label}: ${format(p.value)}, ${(share * 100).toFixed(1)}%`}
                onPointerEnter={() => setHover(p.key)} onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(p.key)} onBlur={() => setHover(null)}>
                {px >= 44 && <span>{Math.round(share * 100)}%</span>}
              </div>
            )
          })}
        </div>
        {hp && (
          <div className="tooltip chart-tip" style={{ top: 34, left: Math.min(W - 120, Math.max(0, shown.slice(0, shown.indexOf(hp)).reduce((a, p) => a + (p.value / total) * (W - gaps) + 2, 0))) }}>
            <div className="muted small">{hp.label}</div>
            <div className="tip-row"><strong>{format(hp.value)}</strong><span className="muted">{((hp.value / total) * 100).toFixed(1)}%</span></div>
          </div>
        )}
      </div>
      <div className="legend legend-below">
        {shown.map((p) => <span key={p.key}><i className={`key-box cat-${p.slot}`} /> {p.label}</span>)}
      </div>
    </figure>
  )
}

/** "View as table" twin under a chart. */
export function TableView({ children }: { children: ReactNode }) {
  return (
    <details className="table-view">
      <summary>View as table</summary>
      <div className="table-scroll">{children}</div>
    </details>
  )
}
