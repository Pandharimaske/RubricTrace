import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, ClipboardList, ChevronRight, AlertTriangle, Trash2 } from 'lucide-react'
import { useExams, useCreateExam, useDeleteExam } from '../hooks/queries'
import { useToast } from '../components/Toast'
import { NEXT_LABEL, nextStep } from '../lib/exam'
import { paths } from '../lib/routes'
import { PageHeader, QueryGate, EmptyState } from '../components/ui'

export default function ExamsPage() {
  const exams = useExams()
  const [creating, setCreating] = useState(false)

  return (
    <div>
      <PageHeader
        title="Exams"
        description="Each exam has one answer key. Upload the class's scripts and grade them all against it."
        actions={
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            <Plus size={16} /> New exam
          </button>
        }
      />

      {creating && <NewExamForm onCancel={() => setCreating(false)} />}

      <QueryGate query={exams} loadingLabel="Loading exams…">
        {(list) =>
          list.length === 0 && !creating ? (
            <EmptyState
              icon={<ClipboardList size={48} />}
              title="No exams yet"
              description="Create an exam, add its answer key, then upload your students' scripts."
              action={<button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={16} /> Create your first exam</button>}
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {list.map((exam) => <ExamCard key={exam.exam_id} exam={exam} />)}
            </div>
          )
        }
      </QueryGate>
    </div>
  )
}

function NewExamForm({ onCancel }) {
  const create = useCreateExam()
  const navigate = useNavigate()
  const toast = useToast()
  const [name, setName] = useState('')

  function submit(e) {
    e.preventDefault()
    if (!name.trim()) return
    create.mutate(name.trim(), {
      onSuccess: (exam) => navigate(paths.exam(exam.exam_id, 'rubric')),
      onError: (err) => toast.error(`Could not create exam: ${err.message}`),
    })
  }

  return (
    <form className="card mb-4" onSubmit={submit}>
      <div className="form-group">
        <label className="form-label" htmlFor="exam-name">Exam name</label>
        <input
          id="exam-name"
          className="form-input"
          placeholder="e.g. Machine Learning — Midterm"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          maxLength={200}
        />
      </div>
      <div className="flex gap-2">
        <button type="submit" className="btn btn-primary btn-sm" disabled={!name.trim() || create.isPending}>
          {create.isPending ? 'Creating…' : 'Create & set up answer key'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

function ExamCard({ exam }) {
  const step = nextStep(exam)
  const del = useDeleteExam()
  const toast = useToast()

  function remove(e) {
    e.preventDefault()
    e.stopPropagation()
    if (!window.confirm(`Delete "${exam.name}"? This removes all ${exam.script_count} uploaded script(s) and their marks. This can't be undone.`)) return
    del.mutate(exam.exam_id, {
      onSuccess: () => toast.success('Exam deleted'),
      onError: (err) => toast.error(`Could not delete exam: ${err.message}`),
    })
  }

  return (
    <Link to={paths.exam(exam.exam_id, step)} className="card interactive" style={{ padding: '16px 20px' }}>
      <div className="flex items-center justify-between">
        <div>
          <div style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text-primary)' }}>{exam.name}</div>
          <div className="text-xs text-muted mt-1">
            {exam.question_count} questions · {exam.total_marks} marks · {exam.script_count} scripts · {exam.graded_count} graded
          </div>
        </div>
        <div className="flex items-center gap-3">
          {exam.review_count > 0 && (
            <span className="badge badge-warning"><AlertTriangle size={11} /> {exam.review_count} to review</span>
          )}
          <button
            className="btn btn-ghost btn-sm btn-icon"
            onClick={remove}
            disabled={del.isPending}
            aria-label={`Delete ${exam.name}`}
            title="Delete exam"
          >
            <Trash2 size={13} />
          </button>
          <span className="btn btn-secondary btn-sm">{NEXT_LABEL[step]} <ChevronRight size={14} /></span>
        </div>
      </div>
    </Link>
  )
}
