/**
 * Minimal single-series charts. Single series → no legend; the title names it.
 * Colors come from --series-1 / --grid / text tokens in index.css (light + dark).
 */
import { useEffect, useRef, useState } from 'react'
import { npr } from '../lib/money'

export function DailyBars({ data, label }: { data: { date: string; value: number }[]; label: string }) {
  const [hover, setHover] = useState<number | null>(null)
  // Draw in real pixels (measured) so text and rounded ends never stretch.
  const wrap = useRef<HTMLDivElement>(null)
  const [W, setW] = useState(640)
  useEffect(() => {
    if (!wrap.current) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.floor(e.contentRect.width))))
    ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])
  const H = 200
  const pad = { l: 8, r: 8, t: 12, b: 24 }
  const max = Math.max(1, ...data.map((d) => d.value))
  const bw = (W - pad.l - pad.r) / data.length
  const barW = Math.max(4, Math.min(48, bw - 6))
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / max)
  const base = H - pad.b
  return (
    <figure className="chart">
      <figcaption className="chart-title">{label}</figcaption>
      <div className="chart-wrap" ref={wrap}>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
          {[0.5, 1].map((f) => (
            <line key={f} x1={pad.l} x2={W - pad.r} y1={y(max * f)} y2={y(max * f)} className="grid" />
          ))}
          <line x1={pad.l} x2={W - pad.r} y1={base} y2={base} className="axis" />
          {data.map((d, i) => {
            const x = pad.l + i * bw + (bw - barW) / 2
            const top = y(d.value)
            const h = Math.max(0, base - top)
            const r = Math.min(4, h, barW / 2)
            // Rounded data end at the top, square at the baseline.
            const path = h
              ? `M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + barW - r} Q${x + barW},${top} ${x + barW},${top + r} V${base} Z`
              : ''
            return (
              <g key={d.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={pad.l + i * bw} y={pad.t} width={bw} height={base - pad.t} fill="transparent" />
                {path && <path d={path} className={hover === i ? 'bar bar-hover' : 'bar'} />}
                {(i % Math.ceil(data.length / 7) === 0 || i === data.length - 1) && (
                  <text x={x + barW / 2} y={H - 6} textAnchor="middle" className="tick">
                    {d.date.slice(5)}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
        {hover != null && (
          <div className="tooltip" style={{ left: `${((hover + 0.5) / data.length) * 100}%` }}>
            <div className="muted small">{data[hover].date}</div>
            <strong>{npr(data[hover].value)}</strong>
          </div>
        )}
      </div>
    </figure>
  )
}

/** Inline magnitude bar for table cells (branch comparison). */
export function InlineBar({ value, max }: { value: number; max: number }) {
  return (
    <span className="inline-bar" aria-hidden>
      <span style={{ width: `${max ? Math.max(2, (value / max) * 100) : 0}%` }} />
    </span>
  )
}
