import React, { useMemo, useState } from 'react'
import { Save, Brain } from 'lucide-react'
import { useToast } from '../components/Toast'
import { useOverrideScore } from '../hooks/queries'
import { paths } from '../lib/routes'
import PageSlider from '../components/PageSlider'
import { ConfidenceBar, PageHeader, SectionLabel, StatusBadge } from '../components/ui'

const byQuestion = (a, b) => a.question_id.localeCompare(b.question_id, undefined, { numeric: true })

/**
 * One student's paper inside an exam: every question with the scanned answer,
 * the extracted text, the AI's marks and reasoning, and a way to override.
 */
export default function ScriptResult({ data }) {
  const { script, student, exam, extractions, evaluations, totals } = data
  const [onlyFlagged, setOnlyFlagged] = useState(false)

  const questions = useMemo(() => {
    const evalById = new Map(evaluations.map((e) => [e.question_id, e]))
    const ids = new Set([...extractions.map((item) => item.question_id), ...evaluations.map((e) => e.question_id)])
    const extractionById = new Map(extractions.map((item) => [item.question_id, item]))
    return [...ids]
      .map((id) => ({ question_id: id, extraction: extractionById.get(id), evaluation: evalById.get(id) }))
      .sort(byQuestion)
  }, [extractions, evaluations])

  const flagged = questions.filter((q) => q.evaluation?.status === 'needs_review').length
  const shown = onlyFlagged ? questions.filter((q) => q.evaluation?.status === 'needs_review') : questions
  const name = student?.name || script.filename

  return (
    <div>
      <PageHeader
        title={name}
        description={`${script.filename}${exam ? ` · ${exam.name}` : ''}`}
        backTo={exam ? paths.exam(exam.exam_id, 'results') : paths.scripts}
        backLabel={exam ? 'Results' : 'All scripts'}
        actions={
          totals?.total_questions > 0 && (
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: '1.6rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                {totals.total_awarded} <span className="text-muted" style={{ fontSize: '1rem' }}>/ {totals.total_max}</span>
              </div>
              <div className="text-xs text-muted">{totals.total_questions} questions graded</div>
            </div>
          )
        }
      />

      {flagged > 0 && (
        <div className="alert alert-warning mb-4 flex items-center justify-between">
          <span>{flagged} answer{flagged > 1 ? 's need' : ' needs'} your review on this paper.</span>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} />
            Show only these
          </label>
        </div>
      )}

      {questions.length === 0 ? (
        <div className="card text-muted text-sm">This script hasn't been processed yet.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {shown.map((q) => (
            <QuestionCard key={q.question_id} scriptId={script.script_id} pageCount={script.page_count} {...q} />
          ))}
        </div>
      )}
    </div>
  )
}

function QuestionCard({ scriptId, pageCount, question_id: qid, extraction, evaluation: ev }) {
  const flagged = ev?.status === 'needs_review'
  const answerText = ev?.answer_text ?? extraction?.extracted_text ?? ''
  const answerPage = ev?.page_number ?? extraction?.page_number ?? null

  return (
    <div className="card" style={{ borderColor: flagged ? 'var(--warning)' : undefined, padding: 20 }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="question-id-pill">{qid}</span>
          {ev && <StatusBadge status={ev.status} />}
        </div>
        {ev ? (
          <div className="flex items-center gap-4">
            <ConfidenceBar value={ev.confidence} />
            <div style={{ fontSize: '1.2rem', fontWeight: 700, color: 'var(--text-primary)' }}>
              {ev.awarded_marks} <span className="text-muted" style={{ fontSize: '0.85rem' }}>/ {ev.max_marks}</span>
            </div>
          </div>
        ) : (
          <span className="text-xs text-muted">Not graded yet</span>
        )}
      </div>

      <div className="grid-2" style={{ alignItems: 'start' }}>
        <div>
          <SectionLabel>Student's answer</SectionLabel>
          {ev?.question_text && (
            <div className="reasoning-block mb-2" style={{ borderColor: 'var(--border-light)' }}>
              <strong>Question:</strong> {ev.question_text}
              {ev.page_number != null && (
                <span className="badge" style={{ marginLeft: 8, fontSize: 11 }}>Page {ev.page_number}</span>
              )}
            </div>
          )}
          <div className="mb-2">
            <PageSlider scriptId={scriptId} answerPage={answerPage} pageCount={pageCount} />
          </div>
          <div className="reasoning-block">{answerText || <span className="text-muted">(no text extracted)</span>}</div>
        </div>

        {ev && (
          <div>
            <SectionLabel><Brain size={12} style={{ display: 'inline', marginRight: 4 }} />Why these marks</SectionLabel>
            {flagged && ev.llm_reasoning?.flag_reasons?.length > 0 && (
              <div className="text-xs mb-2" style={{ color: 'var(--warning-light)' }}>
                Flagged: {ev.llm_reasoning.flag_reasons.join(' · ')}
              </div>
            )}
            <div className="reasoning-block mb-2">{ev.reasoning || <span className="text-muted">(no reasoning recorded)</span>}</div>

            {ev.evidence?.length > 0 && (
              <ul className="text-xs" style={{ paddingLeft: 18, margin: '0 0 8px', color: 'var(--text-secondary)' }}>
                {ev.evidence.map((line, i) => <li key={i}>{line}</li>)}
              </ul>
            )}

            {ev.rubric?.length > 0 && (
              <div className="text-xs text-muted mb-2">
                Marking scheme: {ev.rubric.map((c) => `${c.name} (${c.marks})`).join(' · ')}
              </div>
            )}

            <details>
              <summary className="text-xs text-muted" style={{ cursor: 'pointer' }}>Reference answer</summary>
              <div className="reasoning-block mt-2">{ev.golden_answer || '(none)'}</div>
            </details>
          </div>
        )}
      </div>

      {ev && <OverrideForm scriptId={scriptId} ev={ev} />}
    </div>
  )
}

function OverrideForm({ scriptId, ev }) {
  const override = useOverrideScore()
  const toast = useToast()
  const [marks, setMarks] = useState(String(ev.awarded_marks))
  const [reason, setReason] = useState(ev.teacher_override_reason || '')

  const parsed = parseFloat(marks)
  const valid = Number.isFinite(parsed) && parsed >= 0 && parsed <= ev.max_marks
  const approved = ev.status === 'teacher_approved'
  const changed = String(parsed) !== String(ev.awarded_marks) || !approved

  function save(e) {
    e.preventDefault()
    if (!valid) return
    override.mutate(
      {
        scriptId,
        questionId: ev.question_id,
        awardedMarks: parsed,
        reason: reason.trim() || 'Teacher approved',
      },
      {
        onSuccess: () => toast.success(`${ev.question_id} saved: ${parsed} marks`),
        onError: (err) => toast.error(err.message),
      },
    )
  }

  return (
    <form onSubmit={save} className="flex items-center gap-2 mt-3" style={{ paddingTop: 12, borderTop: '1px solid var(--border)' }}>
      <label className="text-xs text-muted" htmlFor={`m-${ev.question_id}`}>Your marks</label>
      <input
        id={`m-${ev.question_id}`}
        className="form-input"
        style={{ width: 80 }}
        type="number" step="0.5" min="0" max={ev.max_marks}
        value={marks}
        onChange={(e) => setMarks(e.target.value)}
      />
      <input
        className="form-input flex-1"
        placeholder="Feedback (optional)"
        aria-label={`Feedback for ${ev.question_id}`}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button className="btn btn-primary btn-sm" type="submit" disabled={!valid || override.isPending || (approved && !changed)}>
        <Save size={14} /> {approved ? 'Update' : 'Approve'}
      </button>
    </form>
  )
}
