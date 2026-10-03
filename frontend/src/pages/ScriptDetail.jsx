import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api'
import {
  ChevronLeft, ChevronDown, ChevronRight, AlertTriangle, CheckCircle,
  Clock, Edit3, Save, X, Eye, EyeOff, Brain, Zap, Star,
  FileText, RefreshCw, Plus, Trash2, BarChart2, ExternalLink
} from 'lucide-react'

// ──────────────────────────────────────────────────────────────────────────────
// RUBRIC BUILDER
// ──────────────────────────────────────────────────────────────────────────────
function RubricBuilder({ questions, onChange }) {
  function updateQuestion(idx, field, value) {
    const qs = [...questions]
    qs[idx] = { ...qs[idx], [field]: value }
    onChange(qs)
  }
  function updateCriterion(qIdx, cIdx, field, value) {
    const qs = [...questions]
    const criteria = [...(qs[qIdx].criteria || [])]
    criteria[cIdx] = { ...criteria[cIdx], [field]: value }
    qs[qIdx] = { ...qs[qIdx], criteria }
    onChange(qs)
  }
  function addCriterion(qIdx) {
    const qs = [...questions]
    qs[qIdx].criteria = [...(qs[qIdx].criteria || []), { name: '', marks: 1, expected_concepts: [], guidance: '' }]
    onChange(qs)
  }
  function removeCriterion(qIdx, cIdx) {
    const qs = [...questions]
    qs[qIdx].criteria = qs[qIdx].criteria.filter((_, i) => i !== cIdx)
    onChange(qs)
  }
  function removeQuestion(idx) {
    onChange(questions.filter((_, i) => i !== idx))
  }
  function addQuestion() {
    onChange([...questions, {
      question_id: `Q${questions.length + 1}`,
      golden_answer: '',
      max_marks: 5,
      criteria: [{ name: 'Answer quality', marks: 5, expected_concepts: [], guidance: '' }],
    }])
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {questions.map((q, qIdx) => (
        <div key={qIdx} className="card" style={{ borderColor: 'var(--border-light)' }}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="question-id-pill">{q.question_id}</span>
              <span className="text-xs text-muted">{q.max_marks} marks</span>
            </div>
            <button className="btn btn-ghost btn-icon btn-sm" onClick={() => removeQuestion(qIdx)}
              id={`btn-remove-q-${qIdx}`} title="Remove question">
              <Trash2 size={14} />
            </button>
          </div>
          <div className="grid-2 mb-3">
            <div className="form-group">
              <label className="form-label">Question ID</label>
              <input className="form-input" value={q.question_id}
                onChange={e => updateQuestion(qIdx, 'question_id', e.target.value)} />
            </div>
            <div className="form-group">
              <label className="form-label">Max Marks</label>
              <input className="form-input" type="number" min="0" step="0.5" value={q.max_marks}
                onChange={e => updateQuestion(qIdx, 'max_marks', parseFloat(e.target.value) || 0)} />
            </div>
          </div>
          <div className="form-group mb-3">
            <label className="form-label">Golden / Model Answer</label>
            <textarea className="form-textarea" rows={3} value={q.golden_answer}
              onChange={e => updateQuestion(qIdx, 'golden_answer', e.target.value)}
              placeholder="Provide the ideal answer for this question…" />
          </div>

          {/* Criteria */}
          <div className="text-xs text-muted mb-2" style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
            Rubric Criteria
          </div>
          {(q.criteria || []).map((c, cIdx) => (
            <div key={cIdx} className="card mb-2" style={{ background: 'var(--bg-elevated)', padding: 14 }}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-muted">Criterion {cIdx + 1}</span>
                <button className="btn btn-ghost btn-icon btn-sm" onClick={() => removeCriterion(qIdx, cIdx)}
                  id={`btn-remove-criterion-${qIdx}-${cIdx}`}>
                  <X size={12} />
                </button>
              </div>
              <div className="grid-2 mb-2">
                <div className="form-group">
                  <label className="form-label">Name</label>
                  <input className="form-input" value={c.name}
                    onChange={e => updateCriterion(qIdx, cIdx, 'name', e.target.value)}
                    placeholder="e.g. Definition accuracy" />
                </div>
                <div className="form-group">
                  <label className="form-label">Marks</label>
                  <input className="form-input" type="number" min="0" step="0.5" value={c.marks}
                    onChange={e => updateCriterion(qIdx, cIdx, 'marks', parseFloat(e.target.value) || 0)} />
                </div>
              </div>
              <div className="form-group mb-2">
                <label className="form-label">Key points for the LLM (semantic, not keyword match)</label>
                <input className="form-input"
                  value={(c.expected_concepts || []).join(', ')}
                  onChange={e => updateCriterion(qIdx, cIdx, 'expected_concepts',
                    e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
                  placeholder="e.g. labeled training data, maps inputs to outputs" />
              </div>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Grading Guidance (optional)</label>
                <input className="form-input" value={c.guidance || ''}
                  onChange={e => updateCriterion(qIdx, cIdx, 'guidance', e.target.value)}
                  placeholder="e.g. Award only if both terms are used correctly" />
              </div>
            </div>
          ))}
          <button className="btn btn-ghost btn-sm" onClick={() => addCriterion(qIdx)}
            id={`btn-add-criterion-${qIdx}`}>
            <Plus size={14} /> Add Criterion
          </button>
        </div>
      ))}
      <button className="btn btn-secondary" onClick={addQuestion} id="btn-add-question">
        <Plus size={16} /> Add Question
      </button>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// QUESTION RESULT CARD
// ──────────────────────────────────────────────────────────────────────────────
function QuestionResultCard({ item, scriptId, onOverride }) {
  const [expanded, setExpanded] = useState(false)
  const [showOverride, setShowOverride] = useState(false)
  const [overrideMark, setOverrideMark] = useState(item.awarded_marks)
  const [overrideReason, setOverrideReason] = useState('')
  const [saving, setSaving] = useState(false)

  const statusClass = item.status === 'needs_review' ? 'needs-review'
    : item.status === 'teacher_approved' ? 'approved' : ''

  const confidencePct = Math.round((item.confidence || 0) * 100)
  const confidenceColor = confidencePct >= 75 ? 'var(--success)'
    : confidencePct >= 50 ? 'var(--warning)' : 'var(--danger)'

  const markPct = item.max_marks > 0 ? (item.awarded_marks / item.max_marks) * 100 : 0

  async function saveOverride() {
    setSaving(true)
    try {
      await api.scripts.override(scriptId, {
        question_id: item.question_id,
        awarded_marks: overrideMark,
        reason: overrideReason,
      })
      setShowOverride(false)
      onOverride()
    } catch (e) {
      alert(e.message)
    }
    setSaving(false)
  }

  return (
    <div className={`question-card ${statusClass}`}>
      {/* Header row */}
      <div className="question-card-header" onClick={() => setExpanded(v => !v)}
        id={`qcard-header-${item.question_id}`}>
        <span className="question-id-pill">{item.question_id}</span>

        {/* Marks */}
        <div className="marks-display">
          <span className="marks-awarded">{item.awarded_marks}</span>
          <span className="marks-sep">/</span>
          <span className="marks-max">{item.max_marks}</span>
        </div>

        {/* Mini progress */}
        <div style={{ width: 80 }}>
          <div className="progress-bar">
            <div className="progress-fill" style={{
              width: `${markPct}%`,
              background: markPct >= 70 ? 'linear-gradient(90deg, var(--success), #059669)'
                : markPct >= 40 ? 'linear-gradient(90deg, var(--warning), #b45309)'
                : 'linear-gradient(90deg, var(--danger), #b91c1c)',
            }} />
          </div>
        </div>

        {/* Status badge */}
        {item.status === 'needs_review' && (
          <span className="badge badge-warning"><AlertTriangle size={10} /> Review</span>
        )}
        {item.status === 'teacher_approved' && (
          <span className="badge badge-success"><CheckCircle size={10} /> Approved</span>
        )}
        {item.status === 'scored' && (
          <span className="badge badge-success"><Zap size={10} /> Scored</span>
        )}

        {/* Confidence */}
        <div className="confidence-bar" style={{ width: 100 }}>
          <div className="confidence-track">
            <div className="confidence-fill" style={{
              width: `${confidencePct}%`,
              background: confidenceColor,
            }} />
          </div>
          <span className="text-xs" style={{ color: confidenceColor, width: 30, textAlign: 'right' }}>
            {confidencePct}%
          </span>
        </div>

        <ChevronDown size={16} color="var(--text-muted)"
          style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
      </div>

      {/* Expanded body */}
      {expanded && (
        <div className="question-card-body">
          {/* Visual Page Image */}
          <div className="mb-4">
            <div className="text-xs text-muted mb-2 flex items-center justify-between" style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
              <span>Scanned Page{item.page_number != null ? ` (Page ${item.page_number})` : ''}</span>
              {item.page_number != null && (
                <a
                  href={api.scripts.pageImageUrl(scriptId, item.page_number)}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-primary flex items-center gap-1"
                  style={{ textTransform: 'none' }}
                >
                  Open full image <ExternalLink size={11} />
                </a>
              )}
            </div>
            <div style={{
              background: '#ffffff',
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: 8,
              textAlign: 'center',
              maxHeight: 220,
              overflowY: 'auto',
            }}>
              {item.page_number != null ? (
                <img
                  src={api.scripts.pageImageUrl(scriptId, item.page_number)}
                  alt={`Scanned page ${item.page_number}, containing the answer to ${item.question_id}`}
                  style={{ maxWidth: '100%', maxHeight: 200, objectFit: 'contain' }}
                  onError={(e) => {
                    e.target.style.display = 'none'
                    e.target.parentElement.innerHTML = '<div style="color:#666;font-size:12px;padding:8px;">Page image not available</div>'
                  }}
                />
              ) : (
                <div style={{ color: '#666', fontSize: 12, padding: 8 }}>No page number recorded for this question</div>
              )}
            </div>
          </div>

          <div className="grid-2 mb-4">
            {/* Student answer text */}
            <div>
              <div className="text-xs text-muted mb-2" style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                Extracted Student Text
              </div>
              <div className="reasoning-block" style={{ borderColor: 'var(--border)' }}>
                {item.answer_text || <span className="text-muted">No answer extracted</span>}
              </div>
            </div>

            {/* Golden answer */}
            {item.golden_answer && (
              <div>
                <div className="text-xs text-muted mb-2" style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                  Golden Answer
                </div>
                <div className="reasoning-block" style={{ borderColor: 'var(--success)', opacity: 0.85 }}>
                  {item.golden_answer}
                </div>
              </div>
            )}
          </div>

          {/* Evidence */}
          {item.evidence?.length > 0 && (
            <div className="mb-4">
              <div className="text-xs text-muted mb-2" style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                Evidence
              </div>
              <ul className="evidence-list">
                {item.evidence.map((ev, i) => {
                  const isFound = ev.toLowerCase().includes('matched') || ev.toLowerCase().includes('found')
                  const isMissing = ev.toLowerCase().includes('missing')
                  return (
                    <li key={i} className={`evidence-item ${isFound ? 'found' : isMissing ? 'missing' : ''}`}>
                      {ev}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          {/* Reasoning */}
          {item.reasoning && (
            <div className="mb-4">
              <div className="text-xs text-muted mb-2" style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                <Brain size={12} style={{ display: 'inline', marginRight: 4 }} />
                AI Reasoning
              </div>
              <div className="reasoning-block">{item.reasoning}</div>
            </div>
          )}

          {/* LLM detailed reasoning */}
          {item.llm_reasoning && typeof item.llm_reasoning === 'object' && (
            <div className="mb-4">
              <div className="text-xs text-muted mb-2" style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                <Star size={12} style={{ display: 'inline', marginRight: 4 }} />
                LLM Grading Details
                {item.llm_model && <span className="badge badge-primary" style={{ marginLeft: 8 }}>{item.llm_model}</span>}
              </div>
              <div className="card" style={{ background: 'var(--bg-elevated)', padding: 14 }}>
                {item.llm_reasoning.concepts_found?.length > 0 && (
                  <div className="mb-2">
                    <div className="text-xs" style={{ color: 'var(--success-light)', fontWeight: 600, marginBottom: 4 }}>
                      ✓ Concepts Found
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {item.llm_reasoning.concepts_found.map((c, i) => (
                        <span key={i} className="badge badge-success">{c}</span>
                      ))}
                    </div>
                  </div>
                )}
                {item.llm_reasoning.concepts_missing?.length > 0 && (
                  <div className="mb-2">
                    <div className="text-xs" style={{ color: 'var(--danger-light)', fontWeight: 600, marginBottom: 4 }}>
                      ✗ Concepts Missing
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {item.llm_reasoning.concepts_missing.map((c, i) => (
                        <span key={i} className="badge badge-danger">{c}</span>
                      ))}
                    </div>
                  </div>
                )}
                {item.llm_reasoning.criteria_satisfied?.length > 0 && (
                  <div className="text-xs text-success mb-1">
                    <strong>Criteria satisfied:</strong> {item.llm_reasoning.criteria_satisfied.join(', ')}
                  </div>
                )}
                {item.llm_reasoning.criteria_failed?.length > 0 && (
                  <div className="text-xs text-danger mb-1">
                    <strong>Criteria failed:</strong> {item.llm_reasoning.criteria_failed.join(', ')}
                  </div>
                )}
                {item.llm_reasoning.review_reason && (
                  <div className="text-xs text-warning">
                    <strong>Review reason:</strong> {item.llm_reasoning.review_reason}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Flag reasons */}
          {item.flag_reasons?.length > 0 && (
            <div className="mb-4">
              {item.flag_reasons.map((r, i) => (
                <div key={i} className="alert alert-warning" style={{ marginBottom: 6 }}>
                  <AlertTriangle size={14} /> {r}
                </div>
              ))}
            </div>
          )}

          {/* Teacher override */}
          {item.teacher_override !== null && item.teacher_override !== undefined && (
            <div className="alert alert-success mb-4">
              <CheckCircle size={14} />
              <div>
                Teacher override: <strong>{item.teacher_override} marks</strong>
                {item.teacher_override_reason && ` — "${item.teacher_override_reason}"`}
              </div>
            </div>
          )}

          {/* Override panel */}
          {(item.status === 'needs_review' || item.status === 'scored') && (
            <div>
              {!showOverride ? (
                <button className="btn btn-warning btn-sm" onClick={() => setShowOverride(true)}
                  id={`btn-override-${item.question_id}`}>
                  <Edit3 size={14} /> Teacher Override
                </button>
              ) : (
                <div className="override-panel">
                  <h4>Override Marks for {item.question_id}</h4>
                  <div className="grid-2 mb-3">
                    <div className="form-group">
                      <label className="form-label">Approved Marks (0–{item.max_marks})</label>
                      <input
                        id={`override-marks-${item.question_id}`}
                        className="form-input"
                        type="number" min="0" max={item.max_marks} step="0.5"
                        value={overrideMark}
                        onChange={e => setOverrideMark(parseFloat(e.target.value))}
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">Reason</label>
                      <input
                        id={`override-reason-${item.question_id}`}
                        className="form-input"
                        placeholder="e.g. Partial credit for method"
                        value={overrideReason}
                        onChange={e => setOverrideReason(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button className="btn btn-success btn-sm" onClick={saveOverride}
                      disabled={saving} id={`btn-save-override-${item.question_id}`}>
                      {saving ? <span className="spinner" /> : <Save size={14} />} Save Override
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setShowOverride(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// MAIN SCRIPT DETAIL PAGE
// ──────────────────────────────────────────────────────────────────────────────
const DEFAULT_QUESTIONS = [
  {
    question_id: 'Q1',
    golden_answer: '',
    max_marks: 5,
    criteria: [{
      name: 'Answer quality',
      marks: 5,
      expected_concepts: [],
      guidance: '',
    }],
  },
]

export default function ScriptDetail({ navigate, scriptId }) {
  const [script, setScript] = useState(null)
  const [extractions, setExtractions] = useState([])
  const [evaluation, setEvaluation] = useState(null)
  const [tab, setTab] = useState('grade')  // 'grade' | 'results'
  const [questions, setQuestions] = useState(DEFAULT_QUESTIONS)
  const [grading, setGrading] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [savedRubrics, setSavedRubrics] = useState([])
  const [error, setError] = useState(null)
  const [rubricMode, setRubricMode] = useState('builder')  // 'builder' | 'json'
  const [rawJson, setRawJson] = useState('')

  const loadData = useCallback(async () => {
    if (!scriptId) return
    try {
      const detail = await api.scripts.get(scriptId)
      setScript(detail.script)
      setExtractions(detail.extractions || [])
      if (detail.evaluations?.length > 0) {
        const totals = detail.totals || {}
        setEvaluation({
          results: detail.evaluations,
          total_awarded_marks: totals.total_awarded || 0,
          total_max_marks: totals.total_max || 0,
          needs_review: totals.needs_review_count || 0,
        })
        setTab('results')
      }
      // Pre-fill question IDs from extracted answers
      if ((detail.extractions || []).length > 0 && questions.length === 1 && questions[0].question_id === 'Q1') {
        const qids = detail.extractions.map(item => item.question_id)
        setQuestions(qids.map(qid => ({
          question_id: qid,
          golden_answer: '',
          max_marks: 5,
          criteria: [{ name: 'Answer quality', marks: 5, expected_concepts: [], guidance: '' }],
        })))
      }
    } catch (e) {
      setError(e.message)
    }
  }, [scriptId])

  useEffect(() => {
    loadData()
    api.rubricConfigs.list()
      .then(d => setSavedRubrics(d.configs || []))
      .catch(() => {})
  }, [loadData])

  async function handleProcessScript() {
    setProcessing(true)
    setError(null)
    try {
      await api.scripts.process(scriptId)
      await loadData()
    } catch (e) {
      setError(`Failed to process script: ${e.message}`)
    } finally {
      setProcessing(false)
    }
  }

  function handleSelectRubric(val) {
    if (!val) return
    if (val === '__preset_5q') {
      setQuestions([
        {
          question_id: 'Q1',
          golden_answer: 'Supervised learning trains on labeled data (input-output pairs), whereas unsupervised learning finds patterns in unlabeled data.',
          max_marks: 5,
          criteria: [
            { name: 'Supervised definition', marks: 2.5, expected_concepts: ['labeled data', 'supervision'], guidance: 'Must mention labeled data.' },
            { name: 'Unsupervised definition', marks: 2.5, expected_concepts: ['unlabeled data', 'clustering'], guidance: 'Must mention unlabeled data.' }
          ]
        },
        {
          question_id: 'Q2',
          golden_answer: 'Overfitting happens when a model learns noise in training data and fails to generalize. Prevent with regularization, dropout, early stopping.',
          max_marks: 5,
          criteria: [
            { name: 'Definition of overfitting', marks: 2.5, expected_concepts: ['memorization', 'noise', 'generalization failure'], guidance: 'Explains noise or variance.' },
            { name: 'Prevention methods', marks: 2.5, expected_concepts: ['regularization', 'dropout', 'early stopping'], guidance: 'At least 2 methods.' }
          ]
        },
        {
          question_id: 'Q3',
          golden_answer: 'Cross entropy loss quantifies how well predicted probabilities match ground truth distribution.',
          max_marks: 4,
          criteria: [
            { name: 'Loss concept', marks: 4, expected_concepts: ['cross entropy', 'probability distribution'], guidance: 'Explains classification loss.' }
          ]
        },
        {
          question_id: 'Q4',
          golden_answer: 'CNN uses convolutional layers for spatial locality and max pooling to reduce dimensions.',
          max_marks: 6,
          criteria: [
            { name: 'Architecture', marks: 6, expected_concepts: ['conv', 'filter', 'pooling'], guidance: 'Convolution and pooling layers.' }
          ]
        },
        {
          question_id: 'Q5',
          golden_answer: 'Precision is TP/(TP+FP) and Recall is TP/(TP+FN). F1 is the harmonic mean.',
          max_marks: 5,
          criteria: [
            { name: 'Definitions & Rigor', marks: 5, expected_concepts: ['true positive', 'false positive', 'harmonic mean'], guidance: 'Precise formulas or clear concepts.' }
          ]
        }
      ])
      return
    }

    if (val === '__preset_30q') {
      const generated = Array.from({ length: 30 }, (_, i) => ({
        question_id: `Q${i + 1}`,
        golden_answer: `Model answer for question ${i + 1}.`,
        max_marks: 3,
        criteria: [
          { name: 'Core concepts', marks: 2, expected_concepts: [], guidance: 'Accurate terminology and understanding' },
          { name: 'Explanation & clarity', marks: 1, expected_concepts: [], guidance: 'Coherent logic' }
        ]
      }))
      setQuestions(generated)
      return
    }

    const cfg = savedRubrics.find(c => c.config_id === val)
    if (cfg) {
      try {
        const parsed = typeof cfg.config_json === 'string' ? JSON.parse(cfg.config_json) : cfg.config_json
        const qs = parsed.questions || parsed || []
        if (Array.isArray(qs) && qs.length > 0) {
          setQuestions(qs)
        }
      } catch (err) {
        setError(`Failed to parse rubric config: ${err.message}`)
      }
    }
  }

  function syncJsonFromBuilder() {
    const payload = {
      script_id: scriptId,
      questions,
    }
    setRawJson(JSON.stringify(payload, null, 2))
  }

  function syncBuilderFromJson() {
    try {
      const parsed = JSON.parse(rawJson)
      setQuestions(parsed.questions || parsed)
    } catch {
      setError('Invalid JSON in rubric editor.')
    }
  }

  async function handleGrade() {
    setGrading(true)
    setError(null)
    try {
      const questionsToGrade = rubricMode === 'json'
        ? (JSON.parse(rawJson)?.questions || JSON.parse(rawJson))
        : questions
      const res = await api.scripts.evaluate(scriptId, {
        script_id: scriptId,
        questions: questionsToGrade,
      })
      setEvaluation(res)
      setTab('results')
    } catch (e) {
      setError(e.message)
    }
    setGrading(false)
  }

  if (!scriptId) {
    return (
      <div className="empty-state">
        <span className="empty-icon">📄</span>
        <div className="empty-state-title">No script selected</div>
        <button className="btn btn-primary mt-4" onClick={() => navigate('scripts')}>Browse Scripts</button>
      </div>
    )
  }

  if (!script) {
    return (
      <div className="text-center" style={{ paddingTop: 60 }}>
        <div className="spinner spinner-lg" style={{ margin: '0 auto' }} />
      </div>
    )
  }

  const passRate = evaluation && evaluation.total_max_marks > 0
    ? Math.round((evaluation.total_awarded_marks / evaluation.total_max_marks) * 100)
    : null

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <button className="btn btn-ghost btn-sm mb-3" onClick={() => navigate('scripts')} id="btn-back-scripts">
          <ChevronLeft size={16} /> All Scripts
        </button>
        <div className="flex items-center justify-between">
          <div>
            <h1>{script.filename}</h1>
            <p className="mt-2">
              Student: <strong style={{ color: 'var(--text-primary)' }}>{script.student_id}</strong>
              {' · '}
              <span className={`badge badge-${script.status === 'graded' ? 'success' : 'default'}`}>{script.status}</span>
            </p>
          </div>
          {evaluation && (
            <div className="text-right">
              <div style={{ fontSize: '2.5rem', fontWeight: 800, color: 'var(--text-primary)', lineHeight: 1 }}>
                {evaluation.total_awarded_marks}
                <span style={{ fontSize: '1.2rem', color: 'var(--text-muted)', fontWeight: 400 }}>
                  /{evaluation.total_max_marks}
                </span>
              </div>
              <div className="text-xs text-muted mt-1">Total Marks · {passRate}%</div>
            </div>
          )}
        </div>
      </div>

      {/* Not-yet-extracted prompt banner */}
      {extractions.length === 0 && (
        <div className="card mb-4 alert alert-warning flex items-center justify-between" style={{ padding: '14px 18px' }}>
          <div className="flex items-center gap-3">
            <AlertTriangle size={20} />
            <div>
              <strong style={{ fontSize: 14 }}>This script hasn't been read yet</strong>
              <div className="text-xs text-muted mt-1">Render the scanned pages and have the AI transcribe each question's answer before grading.</div>
            </div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={handleProcessScript} disabled={processing} id="btn-process-script">
            {processing ? <><span className="spinner" /> Reading Script…</> : <>🔍 Extract Questions</>}
          </button>
        </div>
      )}

      {/* Extracted questions preview */}
      {extractions.length > 0 && (
        <div className="card mb-4">
          <div className="card-header">
            <h3 className="card-title">Extracted Questions ({extractions.length})</h3>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {extractions.map(item => (
              <div key={item.question_id} className="card interactive" style={{ padding: '10px 14px', minWidth: 120 }}>
                <div className="question-id-pill" style={{ marginBottom: 6 }}>{item.question_id}</div>
                <div className="text-xs text-muted truncate" style={{ maxWidth: 200 }}>
                  {item.extracted_text ? item.extracted_text.slice(0, 60) + (item.extracted_text.length > 60 ? '…' : '') : 'No text'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="tabs">
        <button className={`tab ${tab === 'grade' ? 'active' : ''}`} onClick={() => setTab('grade')}
          id="tab-grade">
          📝 Grade
        </button>
        <button className={`tab ${tab === 'results' ? 'active' : ''}`} onClick={() => setTab('results')}
          id="tab-results" disabled={!evaluation}>
          📊 Results {evaluation && `(${evaluation.results?.length || 0})`}
        </button>
      </div>

      {/* Grade tab */}
      {tab === 'grade' && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 style={{ color: 'var(--text-primary)', margin: 0 }}>Configure Rubric & Golden Answers</h3>
              <p className="text-xs text-muted mt-1">Define reference answer and criteria per question or load an exam template.</p>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="form-input text-xs"
                style={{ width: 'auto', minWidth: 200, padding: '6px 10px' }}
                onChange={e => handleSelectRubric(e.target.value)}
                defaultValue=""
                id="select-load-rubric"
              >
                <option value="" disabled>Load Rubric Template / Saved...</option>
                <option value="__preset_5q">Template: 5-Question ML Exam</option>
                <option value="__preset_30q">Template: 30-Question Standard (90 marks)</option>
                {savedRubrics.map(cfg => (
                  <option key={cfg.config_id} value={cfg.config_id}>
                    Saved: {cfg.name}
                  </option>
                ))}
              </select>
              <button className={`btn btn-sm ${rubricMode === 'builder' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => { setRubricMode('builder') }} id="btn-rubric-builder">
                Builder
              </button>
              <button className={`btn btn-sm ${rubricMode === 'json' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => { setRubricMode('json'); syncJsonFromBuilder() }} id="btn-rubric-json">
                JSON
              </button>
            </div>
          </div>

          {rubricMode === 'builder' ? (
            <RubricBuilder questions={questions} onChange={setQuestions} />
          ) : (
            <div className="mb-4">
              <div className="form-group">
                <label className="form-label">Rubric JSON</label>
                <textarea
                  id="rubric-json-editor"
                  className="json-editor"
                  value={rawJson}
                  onChange={e => setRawJson(e.target.value)}
                  rows={20}
                />
              </div>
              <button className="btn btn-secondary btn-sm" onClick={syncBuilderFromJson}
                id="btn-parse-json">
                Parse & apply
              </button>
            </div>
          )}

          {error && <div className="alert alert-danger mt-3"><AlertTriangle size={14} /> {error}</div>}

          <div className="flex gap-3 mt-4">
            <button className="btn btn-primary btn-lg" onClick={handleGrade} disabled={grading}
              id="btn-grade-script">
              {grading ? <><span className="spinner" /> Grading…</> : <><Brain size={18} /> Grade All Questions</>}
            </button>
            {evaluation && (
              <button className="btn btn-secondary" onClick={() => setTab('results')} id="btn-view-results">
                <BarChart2 size={16} /> View Results
              </button>
            )}
          </div>
        </div>
      )}

      {/* Results tab */}
      {tab === 'results' && evaluation && (
        <div>
          {/* Summary bar */}
          <div className="stats-grid mb-4" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
            <div className="stat-card" style={{ '--accent': '#6366f1' }}>
              <div className="stat-label">Total Marks</div>
              <div className="stat-value">{evaluation.total_awarded_marks}</div>
            </div>
            <div className="stat-card" style={{ '--accent': '#3b82f6' }}>
              <div className="stat-label">Max Marks</div>
              <div className="stat-value">{evaluation.total_max_marks}</div>
            </div>
            <div className="stat-card" style={{ '--accent': '#f59e0b' }}>
              <div className="stat-label">Needs Review</div>
              <div className="stat-value">{evaluation.needs_review}</div>
            </div>
            <div className="stat-card" style={{ '--accent': '#10b981' }}>
              <div className="stat-label">Score %</div>
              <div className="stat-value">{passRate ?? '—'}%</div>
            </div>
          </div>

          {evaluation.needs_review > 0 && (
            <div className="alert alert-warning mb-4">
              <AlertTriangle size={16} />
              <strong>{evaluation.needs_review} question(s)</strong> flagged for teacher review.
              Expand each to approve or override.
            </div>
          )}

          {/* Question cards */}
          {evaluation.results?.map(item => (
            <QuestionResultCard
              key={item.question_id}
              item={item}
              scriptId={scriptId}
              onOverride={loadData}
            />
          ))}

          <div className="mt-4 flex gap-2">
            <button className="btn btn-secondary" onClick={() => setTab('grade')} id="btn-back-grade">
              ← Edit Rubric
            </button>
            <button className="btn btn-ghost" onClick={loadData} id="btn-refresh-results">
              <RefreshCw size={16} /> Refresh
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
