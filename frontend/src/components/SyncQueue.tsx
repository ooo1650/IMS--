import { useState } from 'react'
import { updateQueued, type QueuedSale } from '../lib/offline'
import { syncQueue } from '../lib/sync'
import { Button, Modal } from './ui'

const total = (q: QueuedSale) => Number(q.payload.client_total_paisa ?? 0) / 100

/** Offline sales still on this till: why any failed, a retry, and a file to
 *  keep so nothing is lost if the browser's storage is cleared. */
export function SyncQueue({ queue, onClose }: { queue: QueuedSale[]; onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  const failed = queue.filter((q) => q.status === 'error')

  const retry = async () => {
    setBusy(true)
    try {
      for (const q of failed) await updateQueued({ ...q, status: 'pending', attempts: 0, error: undefined })
      await syncQueue()
    } finally {
      setBusy(false)
    }
  }

  const download = () => {
    const blob = new Blob([JSON.stringify(queue, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `unsynced-sales-${new Date().toISOString().slice(0, 19).replace(/:/g, '')}.json`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <Modal title="Sales waiting to sync" onClose={onClose} wide>
      <p className="muted small">
        These sales were taken offline and are stored only on this till. Keep a copy before clearing browser data.
      </p>
      <table className="table">
        <thead>
          <tr><th>Taken</th><th>Slip</th><th className="num">Total (Rs)</th><th>Status</th></tr>
        </thead>
        <tbody>
          {queue.map((q) => (
            <tr key={q.idempotency_key}>
              <td>{new Date(q.created_at).toLocaleString()}</td>
              <td className="fig">{q.receipt_seq}</td>
              <td className="num">{total(q).toFixed(2)}</td>
              <td>{q.status === 'error' ? <span className="bad">{q.error || 'Rejected'} (tried {q.attempts}×)</span> : 'Waiting'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="modal-foot">
        <Button onClick={download}>Download a copy</Button>
        {failed.length > 0 && <Button kind="primary" busy={busy} onClick={() => void retry()}>Retry failed</Button>}
      </div>
    </Modal>
  )
}
