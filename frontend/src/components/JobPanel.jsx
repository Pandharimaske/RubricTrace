import React from 'react'
import { AlertTriangle, CheckCircle, X } from 'lucide-react'
import { isJobActive } from '../hooks/queries'

/**
 * Progress for an exam's background job. Shows a live bar while running,
 * and the outcome (or error) of the last run of this kind afterwards.
 */
export default function JobPanel({ job, kind, verb, unit, onCancel, cancelling }) {
  if (!job || job.kind !== kind) return null

  if (isJobActive(job)) {
    const pct = job.total ? Math.round((100 * job.done) / job.total) : 0
    return (
      <div className="card mb-4" role="status" aria-live="polite">
        <div className="flex items-center justify-between mb-2">
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            {verb}… {job.done} of {job.total} {unit}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={cancelling || job.cancel_requested}>
            <X size={14} /> {job.cancel_requested ? 'Stopping…' : 'Stop'}
          </button>
        </div>
        <div className="progress" aria-hidden="true">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        {job.message && <div className="text-xs text-muted mt-2 truncate">{job.message}</div>}
      </div>
    )
  }

  if (job.status === 'failed') {
    return (
      <div className="alert alert-danger mb-4" role="alert">
        <AlertTriangle size={16} /> <div>{job.error || 'The job failed.'}</div>
      </div>
    )
  }
  if (job.status === 'cancelled') {
    return (
      <div className="alert alert-warning mb-4">
        <AlertTriangle size={16} /> <div>Stopped after {job.done} of {job.total} {unit}. Nothing was lost — start again to continue.</div>
      </div>
    )
  }
  if (job.error) {
    return (
      <div className="alert alert-warning mb-4">
        <AlertTriangle size={16} /> <div>Finished, but {job.error}.</div>
      </div>
    )
  }
  return (
    <div className="alert alert-success mb-4">
      <CheckCircle size={16} /> <div>Finished {job.total} {unit}.</div>
    </div>
  )
}
