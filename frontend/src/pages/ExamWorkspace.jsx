import React from 'react'
import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'
import { Check, Trash2 } from 'lucide-react'
import { useDeleteExam, useExam, useExamJob } from '../hooks/queries'
import { useToast } from '../components/Toast'
import { stepStates } from '../lib/exam'
import { EXAM_STEPS, paths } from '../lib/routes'
import { guardNavigation } from '../lib/unsaved'
import { PageHeader, QueryGate } from '../components/ui'

/**
 * One exam, five steps: answer key → scripts → grade → review → results.
 * The stepper shows what is done; child routes get { exam, job } via Outlet context.
 */
export default function ExamWorkspace() {
  const { examId } = useParams()
  const query = useExam(examId)
  const { job } = useExamJob(examId)
  const del = useDeleteExam()
  const toast = useToast()
  const navigate = useNavigate()

  function remove(exam) {
    if (!window.confirm(`Delete "${exam.name}"? This removes all ${exam.script_count} uploaded script(s) and their marks. This can't be undone.`)) return
    del.mutate(exam.exam_id, {
      onSuccess: () => { toast.success('Exam deleted'); navigate(paths.exams) },
      onError: (err) => toast.error(`Could not delete exam: ${err.message}`),
    })
  }

  return (
    <QueryGate query={query} loadingLabel="Loading exam…">
      {(exam) => (
        <div>
          <PageHeader
            title={exam.name}
            backTo={paths.exams}
            backLabel="All exams"
            description={`${exam.question_count} questions · ${exam.total_marks} marks · ${exam.script_count} scripts`}
            actions={
              <button className="btn btn-ghost btn-sm" onClick={() => remove(exam)} disabled={del.isPending}>
                <Trash2 size={14} /> Delete exam
              </button>
            }
          />
          <Stepper exam={exam} />
          <Outlet context={{ exam, job }} />
        </div>
      )}
    </QueryGate>
  )
}

function Stepper({ exam }) {
  const states = stepStates(exam)
  return (
    <nav className="stepper" aria-label="Exam steps">
      {EXAM_STEPS.map((step, i) => {
        const done = states[step.key] === 'done'
        const count = step.key === 'review' ? exam.review_count : 0
        return (
          <NavLink
            key={step.key}
            to={paths.exam(exam.exam_id, step.key)}
            onClick={guardNavigation}
            className={({ isActive }) => `step ${isActive ? 'active' : ''} ${done ? 'done' : ''}`}
          >
            <span className="step-dot">{done ? <Check size={12} /> : i + 1}</span>
            <span>{step.label}</span>
            {count > 0 && <span className="nav-badge warning">{count}</span>}
          </NavLink>
        )
      })}
    </nav>
  )
}
