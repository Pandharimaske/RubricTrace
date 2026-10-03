import React from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { AlertTriangle, Download } from 'lucide-react'
import { api } from '../../api'
import { EmptyState, QueryGate } from '../../components/ui'
import { useExamResults } from '../../hooks/queries'
import { paths } from '../../lib/routes'

export default function ExamResults() {
  const { exam } = useOutletContext()
  const results = useExamResults(exam.exam_id)
  const questionText = Object.fromEntries((exam.questions || []).map((q) => [q.question_id, q.question_text]))
  return (
    <QueryGate query={results} loadingLabel="Calculating results…">
      {(data) => <ResultsBody exam={exam} results={data} questionText={questionText} />}
    </QueryGate>
  )
}

const TITLES = {
  needs_review: 'Suggested by the AI — waiting for your review',
  teacher_approved: 'Approved by the teacher',
  scored: 'Graded by the AI',
}

function Cell({ mark }) {
  if (!mark) return <span className="text-muted">–</span>
  const tone =
    mark.status === 'needs_review' ? 'mark-review' : mark.status === 'teacher_approved' ? 'mark-approved' : ''
  return (
    <span className={`mark-cell ${tone}`} title={TITLES[mark.status] || mark.status}>
      {mark.awarded}
    </span>
  )
}

function ResultsBody({ exam, results, questionText }) {
  const graded = results.rows.filter((r) => r.graded_questions > 0)
  if (graded.length === 0) {
    return (
      <EmptyState
        icon="📊"
        title="No marks yet"
        description="Results appear here once scripts have been graded."
        action={<Link to={paths.exam(exam.exam_id, 'grade')} className="btn btn-primary">Go to grading</Link>}
      />
    )
  }

  const percentages = graded.map((r) => r.percentage ?? 0)
  const average = percentages.reduce((a, b) => a + b, 0) / percentages.length
  const pending = graded.reduce((n, r) => n + r.needs_review_count, 0)
  const finalCount = graded.filter((r) => r.final).length

  const summary = [
    ['Class average', `${average.toFixed(1)}%`],
    ['Highest', `${Math.max(...percentages).toFixed(1)}%`],
    ['Lowest', `${Math.min(...percentages).toFixed(1)}%`],
    ['Final', `${finalCount} of ${results.rows.length}`],
  ]

  return (
    <div>
      <div className="stats-grid mb-4">
        {summary.map(([label, value]) => (
          <div key={label} className="stat-card" style={{ '--accent': '#6366f1' }}>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value}</div>
          </div>
        ))}
      </div>

      {pending > 0 && (
        <div className="alert alert-warning mb-4 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <AlertTriangle size={16} />
            Totals include the AI's suggested marks for {pending} answer{pending > 1 ? 's' : ''} still waiting for your review.
          </span>
          <Link to={paths.exam(exam.exam_id, 'review')} className="btn btn-warning btn-sm">Review them</Link>
        </div>
      )}

      <div className="flex items-center justify-between mb-3">
        <div className="text-xs text-muted">
          <span className="mark-cell mark-approved">12</span> teacher approved ·{' '}
          <span className="mark-cell mark-review">12</span> awaiting review · plain = graded by AI
        </div>
        <a className="btn btn-secondary btn-sm" href={api.exams.exportUrl(exam.exam_id)} download>
          <Download size={14} /> Export CSV
        </a>
      </div>

      <div className="table-wrapper results-table">
        <table>
          <thead>
            <tr>
              <th className="sticky-col">Student</th>
              {results.question_ids.map((q) => (
                <th key={q} className="text-center" title={questionText[q] || undefined}>
                  {q}
                  <div className="text-xs text-muted" style={{ fontWeight: 400 }}>/{results.max_by_question[q]}</div>
                </th>
              ))}
              <th className="text-center">Total</th>
              <th className="text-center">%</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {results.rows.map((row) => (
              <tr key={row.script_id}>
                <td className="sticky-col" style={{ fontWeight: 600 }}>
                  <Link to={paths.script(row.script_id)} style={{ color: 'var(--text-primary)', textDecoration: 'none' }}>
                    {row.student_name || row.filename}
                  </Link>
                </td>
                {results.question_ids.map((q) => (
                  <td key={q} className="text-center"><Cell mark={row.marks[q]} /></td>
                ))}
                <td className="text-center" style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                  {row.graded_questions ? `${row.total_awarded} / ${row.total_max}` : '–'}
                </td>
                <td className="text-center">{row.percentage != null && row.graded_questions ? `${row.percentage}%` : '–'}</td>
                <td>
                  {row.final ? (
                    <span className="badge badge-success">Final</span>
                  ) : row.needs_review_count > 0 ? (
                    <span className="badge badge-warning">{row.needs_review_count} to review</span>
                  ) : (
                    <span className="badge badge-default">Not fully graded</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
