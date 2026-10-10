import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Plus, Trash2, ChevronDown, ChevronRight, Upload, Download, Save,
  AlertTriangle, CheckCircle, Sparkles, Layers,
} from 'lucide-react'
import {
  blankCriterion, blankOption, blankQuestion, criteriaTotal, downloadJson, hasErrors, parseImport,
  splitEvenly, TEMPLATE, toDraft, toPayload, totalMarks, validateDraft,
} from '../lib/rubric'
import { useToast } from './Toast'
import { setUnsaved } from '../lib/unsaved'
import { EmptyState, SectionLabel } from './ui'

const nextQuestionId = (draft) => {
  const nums = draft.map((q) => parseInt(q.question_id.replace(/\D/g, ''), 10)).filter(Number.isFinite)
  return `Q${(nums.length ? Math.max(...nums) : 0) + 1}`
}

/**
 * Edits an exam's answer key: every question's reference answer, max marks
 * and (optionally) rubric criteria. Parent should remount it (via `key`) after
 * a successful save so the "unsaved changes" baseline resets.
 */
export default function RubricEditor({ questions, evaluatorConfigs = [], onSave, onImportMetadata, saving }) {
  const toast = useToast()
  const [draft, setDraft] = useState(() => toDraft(questions))
  const [open, setOpen] = useState({})
  const baseline = useRef(JSON.stringify(toPayload(toDraft(questions))))
  const fileInput = useRef(null)

  const validation = useMemo(() => validateDraft(draft), [draft])
  const dirty = JSON.stringify(toPayload(draft)) !== baseline.current
  const missingAnswers = Object.keys(validation.warnings).length

  // Warn before losing edits by closing the tab or clicking a navigation link.
  useEffect(() => {
    setUnsaved(dirty)
    return () => setUnsaved(false)
  }, [dirty])

  useEffect(() => {
    if (!dirty) return undefined
    const warn = (e) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const patchQuestion = (key, patch) =>
    setDraft((d) => d.map((q) => (q._key === key ? { ...q, ...patch } : q)))
  const replaceQuestion = (key, next) =>
    setDraft((d) => d.map((q) => (q._key === key ? next : q)))
  const removeQuestion = (key) => setDraft((d) => d.filter((q) => q._key !== key))

  function addQuestion() {
    const q = blankQuestion(nextQuestionId(draft), draft.at(-1)?.max_marks ?? 5)
    setDraft((d) => [...d, q])
    setOpen((o) => ({ ...o, [q._key]: true }))
  }

  function generate(count, marks) {
    setDraft(Array.from({ length: count }, (_, i) => blankQuestion(`Q${i + 1}`, marks)))
  }

  async function importFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    try {
      const imported = parseImport(await file.text())
      if (draft.length && !window.confirm(`Replace the ${draft.length} questions you have with ${imported.length} imported ones?`)) return
      setDraft(imported)
      if (imported.metadata && onImportMetadata) {
        onImportMetadata(imported.metadata)
      }
      toast.success(`Imported ${imported.length} questions`)
    } catch (err) {
      toast.error(err.message)
    }
  }

  function save() {
    if (hasErrors(validation)) {
      setOpen((o) => ({ ...o, ...Object.fromEntries(Object.keys(validation.errors).map((k) => [k, true])) }))
      toast.error('Fix the highlighted questions before saving.')
      return
    }
    onSave(toPayload(draft))
  }

  if (draft.length === 0) {
    return <QuickSetup onGenerate={generate} onImport={() => fileInput.current?.click()} fileInput={fileInput} onFile={importFile} />
  }

  return (
    <div>
      <div className="card mb-4 flex items-center justify-between" style={{ padding: '12px 16px' }}>
        <div className="text-sm">
          <strong>{draft.length}</strong> questions · <strong>{totalMarks(draft)}</strong> total marks
          {missingAnswers > 0 && (
            <span className="text-muted"> · {missingAnswers} incomplete (missing question text or reference answer)</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-ghost btn-sm" onClick={() => setOpen(Object.fromEntries(draft.map((q) => [q._key, true])))}>
            Expand all
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => setOpen({})}>Collapse all</button>
          <button className="btn btn-ghost btn-sm" onClick={() => fileInput.current?.click()}>
            <Upload size={14} /> Import JSON
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => downloadJson('answer-key.json', { questions: toPayload(draft) })}>
            <Download size={14} /> Export
          </button>
          <input ref={fileInput} type="file" accept=".json,application/json" onChange={importFile} style={{ display: 'none' }} />
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {draft.map((q, index) => (
          <QuestionRow
            key={q._key}
            q={q}
            index={index}
            isOpen={Boolean(open[q._key])}
            toggle={() => setOpen((o) => ({ ...o, [q._key]: !o[q._key] }))}
            errors={validation.errors[q._key]}
            warnings={validation.warnings[q._key]}
            onPatch={(patch) => patchQuestion(q._key, patch)}
            onReplace={(next) => replaceQuestion(q._key, next)}
            onRemove={() => removeQuestion(q._key)}
            evaluatorConfigs={evaluatorConfigs}
          />
        ))}
      </div>

      <button className="btn btn-secondary mt-3" onClick={addQuestion}>
        <Plus size={14} /> Add question
      </button>

      <div className="sticky-bar">
        <span className="text-sm">
          {dirty ? <strong style={{ color: 'var(--warning-light)' }}>Unsaved changes</strong> : <span className="text-muted">All changes saved</span>}
        </span>
        <button className="btn btn-primary" onClick={save} disabled={saving || !dirty}>
          <Save size={16} /> {saving ? 'Saving…' : 'Save answer key'}
        </button>
      </div>
    </div>
  )
}

function QuickSetup({ onGenerate, onImport, fileInput, onFile }) {
  const [count, setCount] = useState(10)
  const [marks, setMarks] = useState(5)
  const valid = Number.isInteger(+count) && +count >= 1 && +count <= 100 && +marks > 0

  return (
    <div className="card" style={{ padding: 28 }}>
      <EmptyState
        icon={<Layers size={40} />}
        title="Start by defining the questions"
        description="Tell us how many questions the paper has. We use this to cut each student's paper into one image per question."
      />
      <div className="grid-2" style={{ maxWidth: 420, margin: '0 auto 16px' }}>
        <div className="form-group">
          <label className="form-label" htmlFor="qs-count">Number of questions</label>
          <input id="qs-count" className="form-input" type="number" min="1" max="100" value={count} onChange={(e) => setCount(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="qs-marks">Marks each (edit later)</label>
          <input id="qs-marks" className="form-input" type="number" min="0.5" step="0.5" value={marks} onChange={(e) => setMarks(e.target.value)} />
        </div>
      </div>
      <div className="flex items-center gap-2" style={{ justifyContent: 'center' }}>
        <button className="btn btn-primary" disabled={!valid} onClick={() => onGenerate(+count, +marks)}>
          <Sparkles size={16} /> Create {valid ? count : ''} questions
        </button>
        <button className="btn btn-ghost" onClick={onImport}><Upload size={14} /> Import answer key (JSON)</button>
        <button className="btn btn-ghost" onClick={() => downloadJson('answer-key-template.json', TEMPLATE)}>
          <Download size={14} /> Get template
        </button>
        <input ref={fileInput} type="file" accept=".json,application/json" onChange={onFile} style={{ display: 'none' }} />
      </div>
    </div>
  )
}

function QuestionRow({ q, index, isOpen, toggle, errors, warnings, onPatch, onReplace, onRemove, evaluatorConfigs }) {
  const hasCriteria = q.criteria.length > 0
  const sumOff = hasCriteria && Math.abs(criteriaTotal(q) - Number(q.max_marks)) > 0.01
  const Chevron = isOpen ? ChevronDown : ChevronRight

  const patchCriterion = (cKey, patch) =>
    onPatch({ criteria: q.criteria.map((c) => (c._key === cKey ? { ...c, ...patch } : c)) })
  const addCriterion = () => {
    const c = blankCriterion()
    if (!hasCriteria) c.marks = q.max_marks
    onPatch({ criteria: [...q.criteria, c] })
  }

  return (
    <div className="card" style={{ padding: 0, borderColor: errors ? 'var(--danger)' : undefined }}>
      <div className="flex items-center gap-3" style={{ padding: '10px 14px' }}>
        <button className="btn btn-ghost btn-sm btn-icon" onClick={toggle} aria-label={isOpen ? 'Collapse' : 'Expand'} aria-expanded={isOpen}>
          <Chevron size={16} />
        </button>
        <input
          className="form-input"
          style={{ width: 90, fontWeight: 600 }}
          value={q.question_id}
          onChange={(e) => onPatch({ question_id: e.target.value })}
          aria-label={`Question ${index + 1} ID`}
          placeholder="Q1"
        />
        <label className="flex items-center gap-2 text-sm text-muted">
          Max marks
          <input
            className="form-input"
            style={{ width: 80 }}
            type="number" min="0" step="0.5"
            value={q.max_marks}
            onChange={(e) => onPatch({ max_marks: e.target.value })}
          />
        </label>
        <select
          className="form-input"
          style={{ width: 150 }}
          value={q.question_type}
          onChange={(e) => onPatch({ question_type: e.target.value })}
          aria-label={`Type for ${q.question_id || `question ${index + 1}`}`}
        >
          <option value="short_answer">Short answer</option>
          <option value="long_answer">Long answer</option>
          <option value="mcq">Multiple choice</option>
          <option value="true_false">True / false</option>
        </select>
        <div className="flex-1 text-xs text-muted truncate">
          {q.question_text.trim() || 'No question text yet'}
        </div>
        {errors ? (
          <span className="badge badge-danger"><AlertTriangle size={11} /> Fix</span>
        ) : warnings ? (
          <span className="badge badge-warning">Incomplete</span>
        ) : (
          <span className="badge badge-success"><CheckCircle size={11} /> Ready</span>
        )}
        <button className="btn btn-ghost btn-sm btn-icon" onClick={onRemove} aria-label={`Delete ${q.question_id || 'question'}`}>
          <Trash2 size={14} />
        </button>
      </div>

      {isOpen && (
        <div style={{ padding: '4px 16px 16px', borderTop: '1px solid var(--border)' }}>
          {errors && (
            <div className="alert alert-danger mt-3" role="alert">
              <AlertTriangle size={16} />
              <div>{errors.map((e) => <div key={e}>{e}</div>)}</div>
            </div>
          )}

          <div className="form-group mt-3">
            <label className="form-label" htmlFor={`qt-${q._key}`}>Question text</label>
            <textarea
              id={`qt-${q._key}`}
              className="form-input"
              style={{ minHeight: 60, resize: 'vertical' }}
              value={q.question_text}
              onChange={(e) => onPatch({ question_text: e.target.value })}
              placeholder="What was actually asked, e.g. 'Explain the difference between supervised and unsupervised learning.'"
            />
          </div>

          <div className="grid-2 mt-3">
            <div className="form-group">
              <label className="form-label" htmlFor={`threshold-${q._key}`}>Review threshold</label>
              <select
                id={`threshold-${q._key}`}
                className="form-input"
                value={q.review_confidence_threshold == null ? '' : q.review_confidence_threshold}
                onChange={(e) => onPatch({ review_confidence_threshold: e.target.value })}
              >
                <option value="">Use exam default</option>
                {[0.55, 0.65, 0.75, 0.85, 0.9, 0.95].map((value) => (
                  <option key={value} value={value}>{Math.round(value * 100)}% confidence</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor={`evaluator-${q._key}`}>Evaluator</label>
              <select
                id={`evaluator-${q._key}`}
                className="form-input"
                value={q.evaluator_config_id || ''}
                onChange={(e) => onPatch({ evaluator_config_id: e.target.value || null })}
              >
                <option value="">Use default evaluator</option>
                {evaluatorConfigs.map((config) => (
                  <option key={config.evaluator_config_id} value={config.evaluator_config_id}>
                    {config.name} · {config.model_name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {(q.question_type === 'mcq' || q.question_type === 'true_false') && (
            <ObjectiveOptions question={q} onPatch={onPatch} />
          )}

          <div className="form-group mt-3">
            <label className="form-label" htmlFor={`ga-${q._key}`}>Reference (golden) answer</label>
            <textarea
              id={`ga-${q._key}`}
              className="form-input"
              style={{ minHeight: 90, resize: 'vertical' }}
              value={q.golden_answer}
              onChange={(e) => onPatch({ golden_answer: e.target.value })}
              placeholder="What a full-marks answer says. The grader compares the student's answer to this."
            />
          </div>

          <div className="flex items-center justify-between mb-2">
            <SectionLabel>Marking scheme (optional)</SectionLabel>
            {hasCriteria && (
              <span className="text-xs" style={{ color: sumOff ? 'var(--danger)' : 'var(--text-muted)' }}>
                {criteriaTotal(q)} of {q.max_marks} marks
                {sumOff && (
                  <button className="btn btn-ghost btn-sm" style={{ marginLeft: 6 }} onClick={() => onReplace(splitEvenly(q))}>
                    Split evenly
                  </button>
                )}
              </span>
            )}
          </div>

          {!hasCriteria && (
            <p className="text-xs text-muted mb-2">
              Without a scheme, the grader awards marks by comparing against the reference answer.
              Add criteria to say exactly where each mark comes from.
            </p>
          )}

          {q.criteria.map((c, i) => (
            <div key={c._key} className="criterion-row">
              <div className="flex items-center gap-2 mb-2">
                <input className="form-input flex-1" placeholder={`Criterion ${i + 1} (e.g. States the formula)`} value={c.name} onChange={(e) => patchCriterion(c._key, { name: e.target.value })} aria-label={`Criterion ${i + 1} name`} />
                <input className="form-input" style={{ width: 80 }} type="number" min="0" step="0.5" value={c.marks} onChange={(e) => patchCriterion(c._key, { marks: e.target.value })} aria-label={`Criterion ${i + 1} marks`} />
                <button className="btn btn-ghost btn-sm btn-icon" onClick={() => onPatch({ criteria: q.criteria.filter((x) => x._key !== c._key) })} aria-label={`Remove criterion ${i + 1}`}>
                  <Trash2 size={13} />
                </button>
              </div>
              <input className="form-input mb-2" placeholder="Key points to look for (comma separated)" value={c.expected_concepts} onChange={(e) => patchCriterion(c._key, { expected_concepts: e.target.value })} aria-label={`Criterion ${i + 1} key points`} />
              <input className="form-input" placeholder="Grading note, e.g. 'accept any correct example' (optional)" value={c.guidance} onChange={(e) => patchCriterion(c._key, { guidance: e.target.value })} aria-label={`Criterion ${i + 1} grading note`} />
            </div>
          ))}

          <button className="btn btn-ghost btn-sm" onClick={addCriterion}>
            <Plus size={13} /> Add criterion
          </button>
        </div>
      )}
    </div>
  )
}

function ObjectiveOptions({ question, onPatch }) {
  const options = question.options || []
  const setOptions = (next) => onPatch({ options: next })
  const update = (key, patch) => setOptions(options.map((option) => option._key === key ? { ...option, ...patch } : option))
  const add = () => setOptions([...options, blankOption()])
  const remove = (key) => setOptions(options.filter((option) => option._key !== key))

  return (
    <div className="form-group mt-3">
      <div className="flex items-center justify-between mb-2">
        <SectionLabel>Answer options</SectionLabel>
        <span className="text-xs text-muted">Select one correct answer</span>
      </div>
      {options.map((option, index) => (
        <div key={option._key} className="flex items-center gap-2 mb-2">
          <input
            className="form-input"
            style={{ width: 60 }}
            value={option.option_key}
            onChange={(e) => update(option._key, { option_key: e.target.value })}
            aria-label={`Option ${index + 1} key`}
            placeholder={String.fromCharCode(65 + index)}
          />
          <input
            className="form-input flex-1"
            value={option.option_text}
            onChange={(e) => update(option._key, { option_text: e.target.value })}
            aria-label={`Option ${index + 1} text`}
            placeholder="Option text"
          />
          <label className="text-xs text-muted flex items-center gap-1">
            <input
              type="radio"
              name={`correct-${question._key}`}
              checked={Boolean(option.is_correct)}
              onChange={() => setOptions(options.map((item) => ({ ...item, is_correct: item._key === option._key })))}
            />
            Correct
          </label>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={() => remove(option._key)} aria-label="Remove option">
            <Trash2 size={13} />
          </button>
        </div>
      ))}
      <button className="btn btn-ghost btn-sm" onClick={add}><Plus size={13} /> Add option</button>
    </div>
  )
}
