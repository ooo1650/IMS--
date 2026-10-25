import { useEffect, useState } from 'react'
import { api, qs, type Page } from '../lib/api'
import type { Product } from '../lib/types'

/** Search-as-you-type product chooser backed by /products/. */
export function ProductPicker({ onPick, placeholder = 'Add product…' }: { onPick: (p: Product) => void; placeholder?: string }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Product[]>([])
  useEffect(() => {
    if (!q.trim()) {
      setResults([])
      return
    }
    const t = setTimeout(() => {
      void api<Page<Product>>(`products/${qs({ q, page_size: 8 })}`).then((p) => setResults(p.results)).catch(() => setResults([]))
    }, 200)
    return () => clearTimeout(t)
  }, [q])
  return (
    <div className="picker">
      <input placeholder={placeholder} value={q} onChange={(e) => setQ(e.target.value)} />
      {results.length > 0 && (
        <div className="picker-results">
          {results.map((p) => (
            <button key={p.id} type="button" onClick={() => { onPick(p); setQ(''); setResults([]) }}>
              <strong>{p.name}</strong> <span className="muted small">{p.sku}{p.on_hand != null ? ` · ${p.on_hand} on hand` : ''}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
