import { api } from '../lib/api'
import { npr } from '../lib/money'
import { ErrorBox, useLoad } from './ui'

/** FY turnover against the CBMS lines and the VAT threshold (Tax & IRD and the dashboard). */
interface Turnover {
  fiscal_year: string; turnover_paisa: number; cbms_threshold_paisa: number; cbms_mandatory_threshold_paisa: number
  vat_threshold_paisa: number; vat_registered: boolean; cbms_enabled: boolean; alerts: { level: 'warn' | 'bad'; message: string }[]
}

export function TurnoverMeter() {
  const { data, error } = useLoad(() => api<Turnover>('compliance/turnover'), [])
  if (error) return <ErrorBox error={error} />
  if (!data) return null
  const bar = (label: string, limit: number) => {
    const pct = Math.min(100, (data.turnover_paisa * 100) / limit)
    return (
      <div className="meter">
        <div className="meter-head"><span>{label}</span><span>{npr(limit)}</span></div>
        <div className="meter-track" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
          <span className={pct >= 100 ? 'meter-bad' : pct >= 80 ? 'meter-warn' : ''} style={{ width: `${Math.max(1, pct)}%` }} />
        </div>
        <div className="muted small">{pct.toFixed(1)}% used</div>
      </div>
    )
  }
  return (
    <section className="card">
      <h2>Turnover FY {data.fiscal_year.replace('-', '/')}: {npr(data.turnover_paisa)}</h2>
      <p className="muted small">Sales excluding VAT, net of credit/debit notes, all branches. CBMS sync is {data.cbms_enabled ? 'on' : 'off'}.</p>
      {data.alerts.map((a) => <div key={a.message} className={`alert alert-${a.level === 'bad' ? 'error' : 'warn'}`}>{a.message}</div>)}
      {bar('CBMS line (budget)', data.cbms_threshold_paisa)}
      {bar('CBMS mandatory (notice 2083/01/04)', data.cbms_mandatory_threshold_paisa)}
      {!data.vat_registered && bar('VAT registration threshold', data.vat_threshold_paisa)}
    </section>
  )
}
