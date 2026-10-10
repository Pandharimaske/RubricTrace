import React, { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, RefreshCw, Brain, Edit3, Save, X, ExternalLink, ChevronRight,
} from 'lucide-react'
import { api } from '../api'
import { useReviewQueue, useOverrideScore } from '../hooks/queries'
import { useToast } from '../components/Toast'
import { paths } from '../lib/routes'
import PageSlider from '../components/PageSlider'
import {
  PageHeader, QueryGate, EmptyState, Modal, ScanImage, SectionLabel,
} from '../components/ui'

const itemKey = (i) => `${i.script_id}::${i.question_id}`
const clip = (s, n) => (s && s.length > n ? `${s.slice(0, n)}…` : s)

export default function ReviewQueue({ examId, embedded = false }) {
  const queue = useReviewQueue(examId)
  const [activeKey, setActiveKey] = useState(null)

  return (
    <div>
      {!embedded && (
        <PageHeader
          title="Teacher Review Queue"
          description="Questions flagged by the AI where confidence was low or teacher judgement is needed."
          actions={
            <button className="btn btn-ghost" onClick={() => queue.refetch()} disabled={queue.isFetching}>
              <RefreshCw size={16} className={queue.isFetching ? 'spin' : ''} /> Refresh
            </button>
          }
        />
      )}
      <QueryGate query={queue} loadingLabel="Checking flagged questions…">
        {(items) => (
          <QueueBody items={items} activeKey={activeKey} setActiveKey={setActiveKey} />
        )}
      </QueryGate>
    </div>
  )
}

function QueueBody({ items, activeKey, setActiveKey }) {
  if (items.length === 0) {
    return (
      <EmptyState
        icon="✅"
        title="All clear — nothing to review"
        description="Every answer met this exam's confidence threshold or has already been approved."
        action={<Link to={paths.scripts} className="btn btn-secondary">Browse graded scripts</Link>}
      />
    )
  }

  const scriptCount = new Set(items.map((i) => i.script_id)).size
  const activeIndex = items.findIndex((i) => itemKey(i) === activeKey)
  const active = activeIndex >= 0 ? items[activeIndex] : null
  const next = activeIndex >= 0 ? items[activeIndex + 1] : null

  return (
    <>
      <div className="alert alert-warning mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle size={18} />
          <span>
            <strong>{items.length} question{items.length !== 1 ? 's' : ''}</strong> need review across{' '}
            {scriptCount} script{scriptCount !== 1 ? 's' : ''}.
          </span>
        </div>
        <button className="btn btn-warning btn-sm" onClick={() => setActiveKey(itemKey(items[0]))}>
          Start reviewing <ChevronRight size={14} />
        </button>
      </div>

      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Crop</th>
              <th>Student</th>
              <th>Question</th>
              <th>AI Marks</th>
              <th>Confidence</th>
              <th>Why flagged</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const conf = Math.round((item.confidence || 0) * 100)
              return (
                <tr key={itemKey(item)}>
                  <td style={{ width: 90 }}>
                    <button
                      className="crop-frame"
                      style={{ width: 80, height: 48, padding: 0, overflow: 'hidden', cursor: 'pointer' }}
                      onClick={() => setActiveKey(itemKey(item))}
                      aria-label={`Review ${item.question_id}`}
                    >
                      {item.page_number != null ? (
                        <ScanImage
                          src={api.scripts.pageImageUrl(item.script_id, item.page_number)}
                          alt=""
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      ) : (
                        <div className="text-xs text-muted" style={{ padding: 4 }}>No page</div>
                      )}
                    </button>
                  </td>
                  <td>
                    <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                      {item.student_name || item.student_id}
                    </div>
                    <div className="text-xs text-muted font-mono truncate" style={{ maxWidth: 160 }}>
                      {item.filename}
                    </div>
                  </td>
                  <td>
                    <span className="question-id-pill">{item.question_id}</span>
                    {item.page_number != null && (
                      <span className="badge" style={{ marginLeft: 6, fontSize: 11 }}>Page {item.page_number}</span>
                    )}
                    {item.question_text && (
                      <div className="text-xs text-muted truncate" style={{ maxWidth: 160, marginTop: 2 }}>
                        {item.question_text}
                      </div>
                    )}
                  </td>
                  <td>
                    <strong style={{ color: 'var(--text-primary)' }}>{item.awarded_marks}</strong>
                    <span className="text-muted"> / {item.max_marks}</span>
                  </td>
                  <td style={{ fontWeight: 600, color: conf >= 50 ? 'var(--warning)' : 'var(--danger)' }}>
                    {conf}%
                  </td>
                  <td className="text-xs" style={{ maxWidth: 260 }}>
                    <div style={{ color: 'var(--warning-light)' }}>
                      {item.flag_reasons?.length
                        ? item.flag_reasons.join(' · ')
                        : clip(item.reasoning, 80) || 'Confidence below threshold'}
                    </div>
                    {item.evidence?.[0] && <div className="text-muted truncate">{item.evidence[0]}</div>}
                  </td>
                  <td className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button className="btn btn-warning btn-sm" onClick={() => setActiveKey(itemKey(item))}>
                        <Edit3 size={13} /> Review
                      </button>
                      <Link
                        to={paths.script(item.script_id)}
                        className="btn btn-ghost btn-sm"
                        title="Open full script"
                        aria-label="Open full script"
                      >
                        <ExternalLink size={13} />
                      </Link>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {active && (
        <ReviewModal
          key={itemKey(active)}
          item={active}
          position={`${activeIndex + 1} of ${items.length}`}
          onClose={() => setActiveKey(null)}
          // After approving, jump straight to the next flagged question.
          onApproved={() => setActiveKey(next ? itemKey(next) : null)}
        />
      )}
    </>
  )
}

function ReviewModal({ item, position, onClose, onApproved }) {
  const override = useOverrideScore()
  const toast = useToast()
  const [marks, setMarks] = useState(String(item.awarded_marks ?? 0))
  const [reason, setReason] = useState('')

  const parsed = parseFloat(marks)
  const valid = Number.isFinite(parsed) && parsed >= 0 && parsed <= item.max_marks

  const approve = useCallback(() => {
    if (!valid || override.isPending) return
    override.mutate(
      {
        scriptId: item.script_id,
        questionId: item.question_id,
        awardedMarks: parsed,
        reason: reason.trim() || 'Teacher approved in review queue',
      },
      {
        onSuccess: () => {
          toast.success(`${item.question_id} approved with ${parsed} marks`)
          onApproved()
        },
        onError: (err) => toast.error(`Override failed: ${err.message}`),
      },
    )
  }, [valid, override, item, parsed, reason, toast, onApproved])

  return (
    <Modal onClose={onClose} width={750}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="question-id-pill">{item.question_id}</span>
          {item.page_number != null && (
            <span className="badge" style={{ fontSize: 11 }}>Page {item.page_number}</span>
          )}
          <h3 style={{ margin: 0 }}>Review answer &amp; score</h3>
          <span className="text-xs text-muted">{position}</span>
        </div>
        <button className="btn btn-ghost btn-icon btn-sm" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </div>

      <div className="text-xs text-muted mb-3 flex items-center gap-3">
        <span>Student: <strong>{item.student_name || item.student_id}</strong></span>
        <span>&bull;</span>
        <span>Script: <strong>{item.filename}</strong></span>
      </div>

      {item.flag_reasons?.length > 0 && (
        <div className="alert alert-warning mb-4">
          <AlertTriangle size={16} />
          <div><strong>Flagged because:</strong> {item.flag_reasons.join(' · ')}</div>
        </div>
      )}

      {item.question_text && (
        <div className="mb-4">
          <SectionLabel>Question asked</SectionLabel>
          <div className="reasoning-block">{item.question_text}</div>
        </div>
      )}

      <div className="mb-4">
        <SectionLabel>Scanned page {item.page_number != null ? `(page ${item.page_number})` : ''}</SectionLabel>
        <PageSlider scriptId={item.script_id} answerPage={item.page_number} pageCount={item.page_count} />
      </div>

      <div className="grid-2 mb-4">
        <div>
          <SectionLabel>Extracted student text</SectionLabel>
          <div className="reasoning-block">
            {item.answer_text || <span className="text-muted">(empty)</span>}
          </div>
        </div>
        <div>
          <SectionLabel>Reference answer</SectionLabel>
          <div className="reasoning-block" style={{ borderColor: 'var(--success)' }}>
            {item.golden_answer || <span className="text-muted">(no reference answer set)</span>}
          </div>
        </div>
      </div>

      {item.reasoning && (
        <div className="mb-4">
          <SectionLabel><Brain size={12} style={{ display: 'inline', marginRight: 4 }} />AI justification</SectionLabel>
          <div className="reasoning-block">{item.reasoning}</div>
        </div>
      )}

      <div className="card" style={{ background: 'var(--bg-elevated)', padding: 16 }}>
        <h4 style={{ marginBottom: 12 }}>Final decision</h4>
        <div className="grid-2 mb-3">
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label" htmlFor="override-marks">
              Marks (0 – {item.max_marks})
            </label>
            <input
              id="override-marks"
              className="form-input"
              type="number"
              step="0.5"
              min="0"
              max={item.max_marks}
              value={marks}
              onChange={(e) => setMarks(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && approve()}
              autoFocus
            />
            {!valid && (
              <div className="form-hint text-danger">Enter a value between 0 and {item.max_marks}.</div>
            )}
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label" htmlFor="override-reason">Feedback (optional)</label>
            <input
              id="override-reason"
              className="form-input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && approve()}
              placeholder="e.g. Partial credit for correct method"
            />
          </div>
        </div>

        <div className="flex items-center justify-between">
          <Link to={paths.script(item.script_id)} className="btn btn-ghost btn-sm">
            <ExternalLink size={14} /> Open full script
          </Link>
          <div className="flex gap-2">
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" onClick={approve} disabled={!valid || override.isPending}>
              <Save size={16} /> {override.isPending ? 'Saving…' : 'Approve & next'}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  )
}
