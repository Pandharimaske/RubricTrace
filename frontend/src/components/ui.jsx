import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ChevronLeft, RefreshCw } from 'lucide-react'

// ── Status badges: one mapping for the whole app ─────────────────────────────
const STATUS = {
  uploaded:         ['Uploaded', 'default'],
  processing:       ['Processing', 'warning'],
  processed:        ['Processed', 'info'],
  scored:           ['Scored', 'success'],
  graded:           ['Graded', 'success'],
  needs_review:     ['Needs review', 'warning'],
  teacher_approved: ['Approved', 'success'],
  error:            ['Error', 'danger'],
}

export function StatusBadge({ status }) {
  const [label, tone] = STATUS[status] || [status || 'Unknown', 'default']
  return <span className={`badge badge-${tone}`}>{label}</span>
}

// ── Page chrome ──────────────────────────────────────────────────────────────
export function PageHeader({ title, description, actions, backTo, backLabel }) {
  useEffect(() => {
    if (typeof title === 'string') document.title = `${title} · RubricTrace`
  }, [title])

  return (
    <div className="page-header">
      {backTo && (
        <Link to={backTo} className="btn btn-ghost btn-sm mb-3">
          <ChevronLeft size={14} /> {backLabel}
        </Link>
      )}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </div>
  )
}

export function SectionLabel({ children }) {
  return <div className="section-label">{children}</div>
}

// ── Loading / empty / error states ───────────────────────────────────────────
export function LoadingState({ label = 'Loading…' }) {
  return (
    <div className="loading-state" role="status">
      <div className="spinner spinner-lg" />
      <p>{label}</p>
    </div>
  )
}

export function EmptyState({ icon, title, description, action }) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-state-icon">{icon}</div>}
      <div className="empty-state-title">{title}</div>
      {description && <div className="empty-state-desc">{description}</div>}
      {action}
    </div>
  )
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="empty-state">
      <AlertTriangle size={36} color="var(--danger)" />
      <div className="empty-state-title">Couldn't load this</div>
      <div className="empty-state-desc">
        {error?.message || 'Something went wrong.'} Check that the API is running.
      </div>
      {onRetry && (
        <button className="btn btn-secondary" onClick={() => onRetry()}>
          <RefreshCw size={14} /> Retry
        </button>
      )}
    </div>
  )
}

/**
 * Handles pending/error for a react-query result so pages only write the
 * happy path:  <QueryGate query={q}>{(data) => <Table rows={data} />}</QueryGate>
 */
export function QueryGate({ query, loadingLabel, children }) {
  if (query.isPending) return <LoadingState label={loadingLabel} />
  if (query.isError) return <ErrorState error={query.error} onRetry={query.refetch} />
  return children(query.data)
}

// ── Data display ─────────────────────────────────────────────────────────────
export function ConfidenceBar({ value = 0, width = 100 }) {
  const pct = Math.round(value * 100)
  const color = pct >= 75 ? 'var(--success)' : pct >= 50 ? 'var(--warning)' : 'var(--danger)'
  return (
    <div className="confidence-bar" style={{ width }} title={`Confidence ${pct}%`}>
      <div className="confidence-track">
        <div className="confidence-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs" style={{ color, width: 30, textAlign: 'right' }}>{pct}%</span>
    </div>
  )
}

/** Scan crop with a fallback that doesn't touch innerHTML. */
export function ScanImage({ src, alt, className, style }) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])

  if (failed) {
    return <div className="text-xs text-muted" style={{ padding: 8 }}>No image crop for this question</div>
  }
  return <img src={src} alt={alt} className={className} style={style} onError={() => setFailed(true)} />
}

// ── Modal ────────────────────────────────────────────────────────────────────
export function Modal({ onClose, width = 600, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal-card" style={{ maxWidth: width }}>{children}</div>
    </div>
  )
}
