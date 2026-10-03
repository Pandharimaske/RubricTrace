import React, { useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { AlertTriangle, ChevronRight, Save } from 'lucide-react'
import RubricEditor from '../../components/RubricEditor'
import ConfidenceThresholdSlider from '../../components/ConfidenceThresholdSlider'
import { useToast } from '../../components/Toast'
import { useEvaluatorConfigs, useSaveExam } from '../../hooks/queries'
import { paths } from '../../lib/routes'

const DEFAULT_THRESHOLD = 0.65

export default function ExamRubric() {
  const { exam } = useOutletContext()
  const save = useSaveExam(exam.exam_id)
  const evaluatorConfigs = useEvaluatorConfigs()
  const toast = useToast()
  // Bumped after each save so the editor remounts with a fresh "unsaved" baseline.
  const [version, setVersion] = useState(0)

  const savedThreshold = exam.review_confidence_threshold ?? DEFAULT_THRESHOLD
  const [threshold, setThreshold] = useState(savedThreshold)
  const thresholdChanged = Math.abs(threshold - savedThreshold) > 0.001

  // Saving the answer key also saves the threshold, so the two can never drift apart.
  function onSave(questions) {
    save.mutate(
      { questions, review_confidence_threshold: threshold },
      {
        onSuccess: () => {
          toast.success('Answer key saved')
          setVersion((v) => v + 1)
        },
        onError: (err) => toast.error(err.message),
      },
    )
  }

  // Explicit, separate save: the threshold is exam configuration, never applied by dragging alone.
  function saveThreshold() {
    save.mutate(
      { review_confidence_threshold: threshold },
      {
        onSuccess: () => toast.success(`Review threshold set to ${Math.round(threshold * 100)}%`),
        onError: (err) => toast.error(err.message),
      },
    )
  }

  return (
    <div>
      <p className="text-sm text-muted mb-4">
        Set this up once for the whole class. Every student's answers are graded against it.
      </p>

      <div className="card mb-4">
        <h3 className="card-title mb-3">Review confidence threshold</h3>
        <p className="text-sm text-muted mb-4">
          Answers the AI is less sure about than this are sent to your review queue. It is part of this
          exam's setup and applies to every student, so everyone is held to the same standard.
        </p>
        <ConfidenceThresholdSlider
          value={threshold}
          onChange={setThreshold}
          disabled={save.isPending}
        />

        {exam.graded_count > 0 && thresholdChanged && (
          <div className="alert alert-warning mt-3">
            <AlertTriangle size={16} />
            <div>
              {exam.graded_count} script{exam.graded_count > 1 ? 's are' : ' is'} already graded. Saving a new
              threshold re-checks them against it, so the review queue may grow or shrink. Answers you have
              already approved stay approved, and nothing is re-graded.
            </div>
          </div>
        )}

        <div className="flex items-center justify-between mt-3">
          <span className="text-xs text-muted">
            {thresholdChanged
              ? <strong style={{ color: 'var(--warning-light)' }}>Unsaved — currently {Math.round(savedThreshold * 100)}%</strong>
              : `Saved at ${Math.round(savedThreshold * 100)}%`}
          </span>
          <div className="flex items-center gap-2">
            {thresholdChanged && (
              <button className="btn btn-ghost btn-sm" onClick={() => setThreshold(savedThreshold)} disabled={save.isPending}>
                Undo
              </button>
            )}
            <button className="btn btn-secondary btn-sm" onClick={saveThreshold} disabled={!thresholdChanged || save.isPending}>
              <Save size={14} /> Save threshold
            </button>
          </div>
        </div>
      </div>

      <RubricEditor
        key={version}
        questions={exam.questions}
        evaluatorConfigs={evaluatorConfigs.data ?? []}
        saving={save.isPending}
        onSave={onSave}
        onImportMetadata={(meta) => {
          if (meta?.review_confidence_threshold != null) {
            setThreshold(meta.review_confidence_threshold)
          }
        }}
      />

      {exam.answer_key_complete && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-sm text-muted">Answer key is complete.</span>
          <Link to={paths.exam(exam.exam_id, 'scripts')} className="btn btn-secondary">
            Next: upload scripts <ChevronRight size={16} />
          </Link>
        </div>
      )}
    </div>
  )
}
