import React from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { CheckCircle, Circle, Play, RotateCcw, ChevronRight } from 'lucide-react'
import JobPanel from '../../components/JobPanel'
import { useToast } from '../../components/Toast'
import { isJobActive, useCancelJob, useStartGrade } from '../../hooks/queries'
import { paths } from '../../lib/routes'

export default function ExamGrade() {
  const { exam, job } = useOutletContext()
  const start = useStartGrade(exam.exam_id)
  const cancel = useCancelJob(exam.exam_id)
  const toast = useToast()

  const running = isJobActive(job)
  const keyReady = exam.answer_key_complete
  const scriptsReady = exam.processed_count > 0
  const canGrade = keyReady && scriptsReady && !running
  const hasGrades = exam.graded_count > 0

  const go = (regrade) =>
    start.mutate({ regrade }, { onError: (e) => toast.error(e.message) })

  function regradeAll() {
    if (window.confirm(
      'Grade every answer again? New marks replace the AI\'s earlier marks. ' +
      'Marks you already approved as the teacher are kept.',
    )) go(true)
  }

  return (
    <div>
      <div className="card mb-4">
        <h3 className="card-title mb-3">Before grading</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Check ok={keyReady} to={paths.exam(exam.exam_id, 'rubric')}>
            {keyReady
              ? `Answer key ready — ${exam.question_count} questions, ${exam.total_marks} marks`
              : 'Answer key needs a reference answer for every question'}
          </Check>
          <Check ok={scriptsReady} to={paths.exam(exam.exam_id, 'scripts')}>
            {scriptsReady
              ? `${exam.processed_count} of ${exam.script_count} scripts processed${exam.error_count ? ` (${exam.error_count} failed)` : ''}`
              : 'No scripts processed yet'}
          </Check>
        </div>
      </div>

      <JobPanel
        job={job}
        kind="grade"
        verb="Grading"
        unit="answers"
        onCancel={() => cancel.mutate()}
        cancelling={cancel.isPending}
      />

      <div className="card">
        <p className="text-sm text-muted mb-3">
          Each answer is compared with the reference answer and marking scheme, marks are awarded
          with written reasoning, and anything the model isn't sure about is sent to your review queue.
          {hasGrades && ' Only answers that haven\'t been graded yet are processed; your approved marks are never overwritten.'}
        </p>
        <div className="flex items-center gap-2">
          <button className="btn btn-primary btn-lg" onClick={() => go(false)} disabled={!canGrade || start.isPending}>
            <Play size={18} /> {hasGrades ? 'Grade remaining answers' : 'Grade the class'}
          </button>
          {hasGrades && (
            <button className="btn btn-secondary" onClick={regradeAll} disabled={!canGrade || start.isPending}>
              <RotateCcw size={16} /> Re-grade everything
            </button>
          )}
        </div>
      </div>

      {hasGrades && !running && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-sm text-muted">
            {exam.graded_count} script{exam.graded_count > 1 ? 's' : ''} graded
            {exam.review_count > 0 ? ` · ${exam.review_count} answers need your review` : ' · nothing left to review'}
          </span>
          <Link
            to={paths.exam(exam.exam_id, exam.review_count > 0 ? 'review' : 'results')}
            className="btn btn-secondary"
          >
            {exam.review_count > 0 ? 'Review flagged answers' : 'View results'} <ChevronRight size={16} />
          </Link>
        </div>
      )}
    </div>
  )
}

function Check({ ok, to, children }) {
  const Icon = ok ? CheckCircle : Circle
  return (
    <div className="flex items-center gap-2 text-sm">
      <Icon size={16} color={ok ? 'var(--success-light)' : 'var(--text-muted)'} />
      <span>{children}</span>
      {!ok && <Link to={to} className="btn btn-ghost btn-sm">Fix</Link>}
    </div>
  )
}
