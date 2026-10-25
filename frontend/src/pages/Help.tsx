/** In-app user manual (payment.md Step 2), rendered from docs/user-manual.md. */
import manual from '../../../docs/user-manual.md?raw'
import { PageHead } from '../components/ui'

interface Item {
  text: string
  sub: string[]
}
type Block = { kind: 'h1' | 'h2' | 'h3' | 'p'; text: string } | { kind: 'ul' | 'ol'; items: Item[] }

/** Markdown subset: headings, paragraphs, bullet/numbered lists with one level
 * of nested bullets, **bold**, *italic*, `code`. */
function parse(md: string): Block[] {
  const blocks: Block[] = []
  let list: { kind: 'ul' | 'ol'; items: Item[] } | null = null
  let para: string[] = []
  const flushPara = () => {
    if (para.length) blocks.push({ kind: 'p', text: para.join(' ') })
    para = []
  }
  const flushList = () => {
    if (list) blocks.push(list)
    list = null
  }
  for (const raw of md.split('\n')) {
    const line = raw.trimEnd()
    const h = /^(#{1,3})\s+(.*)$/.exec(line)
    const nested = /^\s{2,}[-*]\s+(.*)$/.exec(line)
    const ul = /^[-*]\s+(.*)$/.exec(line)
    const ol = /^\d+\.\s+(.*)$/.exec(line)
    if (h) {
      flushPara()
      flushList()
      blocks.push({ kind: (['h1', 'h2', 'h3'] as const)[h[1].length - 1], text: h[2] })
    } else if (nested && list) {
      list.items[list.items.length - 1].sub.push(nested[1])
    } else if (ul || ol) {
      flushPara()
      const kind = ul ? 'ul' : 'ol'
      if (!list || list.kind !== kind) {
        flushList()
        list = { kind, items: [] }
      }
      list.items.push({ text: (ul ?? ol)![1], sub: [] })
    } else if (!line.trim()) {
      flushPara()
    } else if (list && /^\s{2,}/.test(raw)) {
      const last = list.items[list.items.length - 1]
      if (last.sub.length) last.sub[last.sub.length - 1] += ' ' + line.trim()
      else last.text += ' ' + line.trim()
    } else {
      flushList()
      para.push(line.trim())
    }
  }
  flushPara()
  flushList()
  return blocks
}

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)/g)
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong>
          : p.startsWith('*') && p.length > 2 ? <em key={i}>{p.slice(1, -1)}</em>
            : p.startsWith('`') ? <code key={i}>{p.slice(1, -1)}</code>
              : <span key={i}>{p}</span>,
      )}
    </>
  )
}

const BLOCKS = parse(manual)

export default function Help() {
  return (
    <div className="manual">
      <PageHead title="Help" />
      <article className="card prose">
        {BLOCKS.map((b, i) => {
          if ('items' in b) {
            const List = b.kind
            return (
              <List key={i}>
                {b.items.map((it, j) => (
                  <li key={j}>
                    <Inline text={it.text} />
                    {it.sub.length > 0 && <ul>{it.sub.map((s, k) => <li key={k}><Inline text={s} /></li>)}</ul>}
                  </li>
                ))}
              </List>
            )
          }
          if (b.kind === 'h1') return null
          const Tag = b.kind
          return <Tag key={i}><Inline text={b.text} /></Tag>
        })}
      </article>
    </div>
  )
}
