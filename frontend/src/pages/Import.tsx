import { useState } from 'react'
import { useAuth } from '../auth'
import { Button, ErrorBox, PageHead, Tabs, Guide } from '../components/ui'
import { api } from '../lib/api'
import { npr } from '../lib/money'

interface Result {
  ok: boolean
  dry_run: boolean
  rows: Record<string, string | number | boolean>[]
  errors: { row: number; error: string }[]
  creates?: number; updates?: number; imports?: number; skips?: number
}

export default function Import() {
  const { canOrg, can, branch } = useAuth()
  const kinds: ['products' | 'opening-stock' | 'staff', string][] = []
  if (canOrg('catalog.manage')) kinds.push(['products', 'Products'])
  if (can('import.run')) kinds.push(['opening-stock', `Opening stock (${branch?.code})`])
  if (canOrg('staff.manage')) kinds.push(['staff', 'Staff list'])
  const [kind, setKind] = useState(kinds[0]?.[0] ?? 'products')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(dryRun: boolean) {
    if (!file) return
    setBusy(true)
    setError(null)
    const form = new FormData()
    form.append('file', file)
    form.append('dry_run', String(dryRun))
    try {
      setResult(await api<Result>(`import/${kind}`, { form }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <PageHead title="Data import">
        <a className="btn btn-default" href={`/api/v1/import/template/${kind}`}>Download template</a>
      </PageHead>
      <Guide id="import" title="Loading many records at once">
        <ol>
          <li>Download the template and fill one row per product (or stock line). Save it as CSV or Excel (.xlsx); for Excel the first sheet is read.</li>
          <li>Upload it and press <strong>Preview (dry run)</strong>: nothing is saved, you see what would change.</li>
          <li>If it looks right, <strong>Commit import</strong>.</li>
        </ol>
      </Guide>
      <Tabs value={kind} onChange={(k) => { setKind(k); setResult(null) }} tabs={kinds} />
      <div className="card">
        <p className="muted">
          {kind === 'staff'
            ? 'One row per person: name, branch code, job, phone, joined (YYYY-MM-DD, AD) and monthly salary in rupees. A name already at that branch is updated, so re-uploading adds nobody twice.'
            : kind === 'products'
            ? 'Upserts on SKU — re-importing the same file updates rather than duplicates. Prices are in rupees (e.g. 120.50).'
            : 'Writes opening stock at the given cost and sets the weighted average. A SKU already imported for this branch is skipped, so re-uploading never doubles stock.'}
        </p>
        <div className="inline-form">
          <input type="file" accept=".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null) }} />
          <Button busy={busy} disabled={!file} onClick={() => void run(true)}>Preview (dry run)</Button>
          <Button kind="primary" busy={busy} disabled={!file || !result?.ok || !result.dry_run}
            why={!file ? null : !result ? 'Preview first' : !result.ok ? 'Fix the rows listed below, then preview again' : null} onClick={() => void run(false)}>Commit import</Button>
        </div>
        <ErrorBox error={error} />
        {result && (
          <>
            <div className={`alert ${result.ok ? (result.dry_run ? 'alert-info' : 'alert-good') : 'alert-error'}`}>
              {result.ok
                ? `${result.dry_run ? 'Preview' : 'Imported'}: ${kind !== 'opening-stock' ? `${result.creates} new, ${result.updates} updated` : `${result.imports} to import, ${result.skips} skipped`}`
                : `${result.errors.length} row(s) have errors — fix them and preview again. Nothing was written.`}
            </div>
            {result.errors.length > 0 && (
              <table className="table">
                <thead><tr><th>Row</th><th>Error</th></tr></thead>
                <tbody>{result.errors.map((e, i) => <tr key={i}><td>{e.row}</td><td className="text-bad">{e.error}</td></tr>)}</tbody>
              </table>
            )}
            {result.rows.length > 0 && (
              <div className="table-scroll tall">
                <table className="table">
                  <thead><tr><th>Row</th><th>{kind === 'staff' ? 'Branch' : 'SKU'}</th><th>{kind === 'opening-stock' ? 'Qty' : 'Name'}</th><th className="num">{kind === 'staff' ? 'Salary' : kind === 'products' ? 'Price' : 'Cost'}</th><th>Action</th></tr></thead>
                  <tbody>
                    {result.rows.map((r) => (
                      <tr key={String(r.row)}>
                        <td>{r.row}</td>
                        <td className="mono">{kind === 'staff' ? r.branch : r.sku}</td>
                        <td>{kind === 'opening-stock' ? r.qty : r.name}</td>
                        <td className="num">{npr(Number(kind === 'staff' ? r.salary_paisa : kind === 'products' ? r.sale_price_paisa : r.unit_cost_paisa))}</td>
                        <td>{r.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
