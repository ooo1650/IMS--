/** Small chart pieces used inside tables. The report charts live in analytics.tsx. */

/** Inline magnitude bar for table cells (branch comparison). */
export function InlineBar({ value, max }: { value: number; max: number }) {
  return (
    <span className="inline-bar" aria-hidden>
      <span style={{ width: `${max ? Math.max(2, (value / max) * 100) : 0}%` }} />
    </span>
  )
}
