import React, { useState, useEffect } from 'react'
import { api } from '../api'
import {
  BookOpen, Plus, Trash2, Edit3, Save, Copy, Download, Upload,
  CheckCircle, AlertCircle, ChevronDown, ChevronRight, Sparkles,
  Layers, CheckSquare, ArrowRight, X, Info
} from 'lucide-react'

// Built-in presets to kickstart grading easily
const PRESET_TEMPLATES = [
  {
    name: 'CS / Machine Learning Exam (5 Questions)',
    description: 'Covers Supervised vs Unsupervised Learning, Overfitting, Loss Functions, CNNs, and Evaluation Metrics.',
    questions: [
      {
        question_id: 'Q1',
        golden_answer: 'Supervised learning trains on labeled data (input-output pairs) to predict outputs, whereas unsupervised learning finds hidden patterns, groupings, or representations in unlabeled data without target labels.',
        max_marks: 5,
        criteria: [
          { name: 'Supervised definition', marks: 2, expected_concepts: ['labeled data', 'ground truth', 'supervision'], guidance: 'Must explicitly mention labeled datasets.' },
          { name: 'Unsupervised definition', marks: 2, expected_concepts: ['unlabeled data', 'clustering', 'patterns'], guidance: 'Must identify unlabeled datasets or discovering intrinsic structure.' },
          { name: 'Key distinction / contrast', marks: 1, expected_concepts: ['target labels', 'feedback'], guidance: 'Clear comparison between the two paradigms.' }
        ]
      },
      {
        question_id: 'Q2',
        golden_answer: 'Overfitting occurs when a model learns the training data too closely including noise and outliers, resulting in high training accuracy but poor generalization to unseen test data. Mitigation techniques include regularization (L1/L2), dropout, cross-validation, and early stopping.',
        max_marks: 5,
        criteria: [
          { name: 'Definition of overfitting', marks: 2.5, expected_concepts: ['memorization', 'noise', 'generalization failure', 'poor test performance'], guidance: 'Explains high variance or memorization of noise.' },
          { name: 'Remediation methods', marks: 2.5, expected_concepts: ['regularization', 'dropout', 'cross-validation', 'early stopping', 'data augmentation'], guidance: 'Mentions at least 2 distinct prevention strategies.' }
        ]
      },
      {
        question_id: 'Q3',
        golden_answer: 'Cross-entropy loss measures the performance of a classification model whose output is a probability value between 0 and 1. It quantifies the difference between two probability distributions: the true distribution and the predicted distribution.',
        max_marks: 4,
        criteria: [
          { name: 'Purpose in classification', marks: 2, expected_concepts: ['classification', 'probability distribution', 'log loss'], guidance: 'States role in classification networks.' },
          { name: 'Mathematical formulation / intuition', marks: 2, expected_concepts: ['negative log likelihood', 'penalizing confident wrong predictions'], guidance: 'Explains why large divergence is penalized.' }
        ]
      },
      {
        question_id: 'Q4',
        golden_answer: 'Convolutional neural networks leverage convolutional layers to detect spatial hierarchies and translation-invariant features, pooling layers to reduce spatial dimensions, and fully connected layers for final classification.',
        max_marks: 6,
        criteria: [
          { name: 'Convolution mechanism', marks: 2, expected_concepts: ['filter', 'kernel', 'feature map', 'spatial locality'], guidance: 'Explains sliding kernel and local feature extraction.' },
          { name: 'Pooling / Subsampling', marks: 2, expected_concepts: ['max pooling', 'dimension reduction', 'invariance'], guidance: 'Downsampling and dimensionality reduction.' },
          { name: 'End-to-end architecture flow', marks: 2, expected_concepts: ['dense layer', 'softmax', 'classification'], guidance: 'How features flow to final prediction.' }
        ]
      },
      {
        question_id: 'Q5',
        golden_answer: 'Precision is the fraction of relevant instances among the retrieved instances (TP / (TP + FP)), while Recall is the fraction of relevant instances that were retrieved (TP / (TP + FN)). The F1-score is the harmonic mean of precision and recall.',
        max_marks: 5,
        criteria: [
          { name: 'Precision definition', marks: 2, expected_concepts: ['true positive', 'false positive', 'accuracy of positive predictions'], guidance: 'Formula or concept: TP / (TP + FP).' },
          { name: 'Recall definition', marks: 2, expected_concepts: ['true positive', 'false negative', 'sensitivity'], guidance: 'Formula or concept: TP / (TP + FN).' },
          { name: 'F1 Score concept', marks: 1, expected_concepts: ['harmonic mean', 'balance'], guidance: 'Harmonic mean balancing both.' }
        ]
      }
    ]
  },
  {
    name: '30-Question Standard Exam Rubric Template',
    description: 'Full 30-question blueprint with 3 marks per question (Total: 90 marks) ready for mass scanning and multi-question evaluation.',
    questions: Array.from({ length: 30 }, (_, i) => ({
      question_id: `Q${i + 1}`,
      golden_answer: `Model answer for question ${i + 1}. Detail the expected key principles, core terminology, and concluding deduction.`,
      max_marks: 3,
      criteria: [
        { name: 'Core Concept & Definition', marks: 1.5, expected_concepts: ['concept', 'accurate terminology'], guidance: 'Student demonstrates understanding of the fundamental principle.' },
        { name: 'Explanation & Evidence', marks: 1.5, expected_concepts: ['reasoning', 'example'], guidance: 'Clear logical progression or appropriate example provided.' }
      ]
    }))
  }
]

export default function RubricConfigs({ navigate }) {
  const [configs, setConfigs] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeConfig, setActiveConfig] = useState(null)
  const [isEditing, setIsEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editQuestions, setEditQuestions] = useState([])
  const [saving, setSaving] = useState(false)
  const [notification, setNotification] = useState(null)
  const [jsonModalOpen, setJsonModalOpen] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [batchModalOpen, setBatchModalOpen] = useState(false)
  const [batchCount, setBatchCount] = useState(10)
  const [batchMarks, setBatchMarks] = useState(5)

  function showNotify(msg, type = 'success') {
    setNotification({ msg, type })
    setTimeout(() => setNotification(null), 4000)
  }

  function fetchConfigs() {
    setLoading(true)
    api.rubricConfigs.list()
      .then(d => {
        setConfigs(d.configs || [])
        setLoading(false)
      })
      .catch(() => {
        setLoading(false)
      })
  }

  useEffect(() => {
    fetchConfigs()
  }, [])

  function startCreateNew() {
    setEditName('New Rubric Config')
    setEditQuestions([
      {
        question_id: 'Q1',
        golden_answer: '',
        max_marks: 5,
        criteria: [
          { name: 'Core understanding', marks: 3, expected_concepts: [], guidance: '' },
          { name: 'Explanation & clarity', marks: 2, expected_concepts: [], guidance: '' }
        ]
      }
    ])
    setIsEditing(true)
    setActiveConfig(null)
  }

  function loadPreset(preset) {
    setEditName(preset.name)
    setEditQuestions(JSON.parse(JSON.stringify(preset.questions)))
    setIsEditing(true)
    setActiveConfig(null)
    showNotify(`Loaded preset "${preset.name}". Make any edits and click Save.`, 'info')
  }

  function editExisting(cfg) {
    try {
      const parsed = typeof cfg.config_json === 'string' ? JSON.parse(cfg.config_json) : cfg.config_json
      const questions = parsed.questions || parsed || []
      setEditName(cfg.name)
      setEditQuestions(Array.isArray(questions) ? questions : [])
      setIsEditing(true)
      setActiveConfig(cfg)
    } catch {
      showNotify('Failed to parse rubric config JSON', 'error')
    }
  }

  async function handleSave() {
    if (!editName.trim()) {
      showNotify('Please give your rubric configuration a name.', 'error')
      return
    }
    if (editQuestions.length === 0) {
      showNotify('Add at least one question to the rubric.', 'error')
      return
    }

    setSaving(true)
    try {
      const payload = {
        name: editName.trim(),
        config: {
          name: editName.trim(),
          total_questions: editQuestions.length,
          questions: editQuestions,
        }
      }
      await api.rubricConfigs.save(payload)
      showNotify('Rubric configuration saved successfully!')
      setIsEditing(false)
      fetchConfigs()
    } catch (err) {
      showNotify(`Failed to save: ${err.message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  // Question editing helpers
  function updateQuestion(idx, field, value) {
    const next = [...editQuestions]
    next[idx] = { ...next[idx], [field]: value }
    setEditQuestions(next)
  }

  function addQuestion() {
    const nextId = `Q${editQuestions.length + 1}`
    setEditQuestions([
      ...editQuestions,
      {
        question_id: nextId,
        golden_answer: '',
        max_marks: 5,
        criteria: [{ name: 'Answer accuracy', marks: 5, expected_concepts: [], guidance: '' }]
      }
    ])
  }

  function removeQuestion(idx) {
    setEditQuestions(editQuestions.filter((_, i) => i !== idx))
  }

  function addCriterion(qIdx) {
    const next = [...editQuestions]
    const cur = next[qIdx].criteria || []
    next[qIdx].criteria = [
      ...cur,
      { name: 'Additional criterion', marks: 1, expected_concepts: [], guidance: '' }
    ]
    setEditQuestions(next)
  }

  function updateCriterion(qIdx, cIdx, field, value) {
    const next = [...editQuestions]
    const criteria = [...(next[qIdx].criteria || [])]
    criteria[cIdx] = { ...criteria[cIdx], [field]: value }
    next[qIdx].criteria = criteria
    setEditQuestions(next)
  }

  function removeCriterion(qIdx, cIdx) {
    const next = [...editQuestions]
    next[qIdx].criteria = (next[qIdx].criteria || []).filter((_, i) => i !== cIdx)
    setEditQuestions(next)
  }

  function addConceptTag(qIdx, cIdx, conceptStr) {
    const trimmed = conceptStr.trim()
    if (!trimmed) return
    const next = [...editQuestions]
    const curConcepts = next[qIdx].criteria[cIdx].expected_concepts || []
    if (!curConcepts.includes(trimmed)) {
      next[qIdx].criteria[cIdx].expected_concepts = [...curConcepts, trimmed]
      setEditQuestions(next)
    }
  }

  function removeConceptTag(qIdx, cIdx, tagIdx) {
    const next = [...editQuestions]
    next[qIdx].criteria[cIdx].expected_concepts = next[qIdx].criteria[cIdx].expected_concepts.filter((_, i) => i !== tagIdx)
    setEditQuestions(next)
  }

  function handleBatchGenerate() {
    const count = parseInt(batchCount, 10) || 5
    const marks = parseFloat(batchMarks) || 5
    const generated = Array.from({ length: count }, (_, i) => ({
      question_id: `Q${i + 1}`,
      golden_answer: `Golden reference answer for Question ${i + 1}.`,
      max_marks: marks,
      criteria: [
        { name: 'Correctness & concepts', marks: Number((marks * 0.7).toFixed(1)), expected_concepts: [], guidance: 'Key expected points covered' },
        { name: 'Clarity and explanation', marks: Number((marks * 0.3).toFixed(1)), expected_concepts: [], guidance: 'Cohesive and sound reasoning' }
      ]
    }))
    setEditQuestions(generated)
    setBatchModalOpen(false)
    showNotify(`Generated ${count} questions at ${marks} marks each.`)
  }

  function exportJSON() {
    const data = {
      name: editName,
      questions: editQuestions,
      exported_at: new Date().toISOString()
    }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${editName.toLowerCase().replace(/[^a-z0-9]/g, '_')}_rubric.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  function openImportModal() {
    setJsonText(JSON.stringify({ name: editName, questions: editQuestions }, null, 2))
    setJsonModalOpen(true)
  }

  function applyImportedJSON() {
    try {
      const parsed = JSON.parse(jsonText)
      if (parsed.name || parsed.exam_title || parsed.title) {
        setEditName(parsed.name || parsed.exam_title || parsed.title)
      }
      let qs = parsed.questions || (Array.isArray(parsed) ? parsed : null)
      if (!qs && Array.isArray(parsed?.sections)) {
        qs = []
        for (const s of parsed.sections) {
          const sType = s.type || s.question_type || 'short_answer'
          const sMarks = s.marks_per_question
          for (const q of (s.questions || [])) {
            qs.push({
              question_id: String(q.question_id ?? (q.id != null ? (/^\d+$/.test(String(q.id)) ? `Q${q.id}` : q.id) : '')),
              question_text: String(q.question_text ?? q.question ?? ''),
              question_type: q.question_type ?? q.type ?? sType,
              max_marks: Number(q.max_marks ?? q.marks ?? sMarks ?? 1),
              golden_answer: String(q.golden_answer ?? q.correct_answer ?? q.model_answer ?? q.answer ?? ''),
              criteria: (q.criteria || q.rubric || []).map((c) => ({
                name: String(c.name ?? c.criterion ?? ''),
                marks: Number(c.marks ?? c.points ?? 1),
                expected_concepts: Array.isArray(c.expected_concepts) ? c.expected_concepts : [],
                guidance: String(c.guidance ?? ''),
              })),
              options: q.options || [],
            })
          }
        }
      }
      if (!qs || qs.length === 0) throw new Error('JSON must contain a "questions" or "sections" array.')
      setEditQuestions(qs)
      setJsonModalOpen(false)
      showNotify(`Imported ${qs.length} questions from JSON.`)
    } catch (err) {
      alert(`Invalid JSON format: ${err.message}`)
    }
  }

  const totalPossibleMarks = editQuestions.reduce((acc, q) => acc + (parseFloat(q.max_marks) || 0), 0)

  return (
    <div>
      {/* Toast Notification */}
      {notification && (
        <div style={{
          position: 'fixed',
          top: 24,
          right: 24,
          zIndex: 9999,
          background: notification.type === 'error' ? 'var(--status-review-bg)' : 'var(--bg-elevated)',
          border: `1px solid ${notification.type === 'error' ? 'var(--status-review-border)' : 'var(--border-light)'}`,
          padding: '12px 20px',
          borderRadius: 8,
          boxShadow: 'var(--shadow-lg)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          color: notification.type === 'error' ? 'var(--status-review-text)' : 'var(--text-primary)',
          fontSize: 14,
        }}>
          {notification.type === 'error' ? <AlertCircle size={16} /> : <CheckCircle size={16} color="var(--primary)" />}
          <span>{notification.msg}</span>
        </div>
      )}

      {/* Header */}
      <div className="page-header flex items-center justify-between">
        <div>
          <h1>Rubric Configurations</h1>
          <p>Create and reuse grading rubrics, golden answers, expected concepts, and mark breakdowns.</p>
        </div>
        <div className="flex items-center gap-2">
          {!isEditing ? (
            <button className="btn btn-primary" onClick={startCreateNew} id="btn-create-rubric">
              <Plus size={16} /> Create Rubric
            </button>
          ) : (
            <>
              <button className="btn btn-ghost" onClick={() => setIsEditing(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving} id="btn-save-rubric">
                <Save size={16} /> {saving ? 'Saving…' : 'Save Config'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* VIEW / EDITING MODES */}
      {!isEditing ? (
        <div>
          {/* Quick Presets Banner */}
          <div className="card mb-4" style={{ background: 'linear-gradient(135deg, rgba(59, 130, 246, 0.08), rgba(99, 102, 241, 0.05))', borderColor: 'rgba(59, 130, 246, 0.25)' }}>
            <div className="flex items-center gap-2 mb-2">
              <Sparkles size={18} color="var(--primary)" />
              <h3 className="card-title" style={{ margin: 0 }}>Starter Presets & Blueprints</h3>
            </div>
            <p className="text-muted text-sm mb-3">
              Get started instantly using pre-configured blueprints designed for multi-question papers and segmented questions.
            </p>
            <div className="grid-2">
              {PRESET_TEMPLATES.map((tmpl, idx) => (
                <div key={idx} className="card" style={{ background: 'var(--bg-elevated)', padding: 16 }}>
                  <div className="flex items-center justify-between mb-1">
                    <h4 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{tmpl.name}</h4>
                    <span className="badge badge-info">{tmpl.questions.length} Questions</span>
                  </div>
                  <p className="text-xs text-muted mb-3">{tmpl.description}</p>
                  <button className="btn btn-secondary btn-sm" onClick={() => loadPreset(tmpl)} id={`btn-preset-${idx}`}>
                    <Copy size={13} /> Load Template
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Saved Configs List */}
          <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>Saved Configurations</h2>
          {loading ? (
            <div className="loading-state">
              <div className="spinner" />
              <p>Loading rubric configurations…</p>
            </div>
          ) : configs.length === 0 ? (
            <div className="empty-state">
              <BookOpen size={40} className="empty-state-icon" />
              <div className="empty-state-title">No Rubric Configs Yet</div>
              <div className="empty-state-desc">
                Save a rubric configuration with golden answers and criteria to use when evaluating scripts.
              </div>
              <button className="btn btn-primary" onClick={startCreateNew}>
                <Plus size={16} /> Create First Rubric
              </button>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {configs.map((c) => {
                let qCount = 0
                let totalMarks = 0
                try {
                  const parsed = typeof c.config_json === 'string' ? JSON.parse(c.config_json) : c.config_json
                  const qs = parsed.questions || parsed || []
                  qCount = qs.length
                  totalMarks = qs.reduce((sum, q) => sum + (parseFloat(q.max_marks) || 0), 0)
                } catch {
                  // ignore
                }

                return (
                  <div key={c.config_id} className="card flex items-center justify-between" style={{ padding: '16px 20px' }}>
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span style={{ fontWeight: 600, fontSize: 16, color: 'var(--text-primary)' }}>{c.name}</span>
                        <span className="badge badge-info">{qCount} questions</span>
                        <span className="badge badge-neutral">{totalMarks} total marks</span>
                      </div>
                      <div className="text-xs text-muted">
                        Config ID: <span className="font-mono">{c.config_id}</span> &bull; Saved {c.created_at?.slice(0, 16)}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <button className="btn btn-secondary btn-sm" onClick={() => editExisting(c)} id={`btn-edit-${c.config_id}`}>
                        <Edit3 size={14} /> Edit & View
                      </button>
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => navigate('scripts')}
                        title="Go to scripts to apply this rubric"
                      >
                        Apply to Script <ArrowRight size={14} />
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      ) : (
        /* EDITING / BUILDER MODE */
        <div>
          {/* Top Bar for Config Name & Actions */}
          <div className="card mb-4">
            <div className="grid-2 mb-3">
              <div className="form-group">
                <label className="form-label">Configuration Name *</label>
                <input
                  className="form-input"
                  value={editName}
                  onChange={e => setEditName(e.target.value)}
                  placeholder="e.g. Midterm Physics Exam - Standard Rubric"
                  id="input-config-name"
                />
              </div>
              <div className="form-group flex flex-col justify-end">
                <div className="flex items-center gap-2">
                  <button className="btn btn-secondary btn-sm" onClick={() => setBatchModalOpen(true)} id="btn-batch-gen">
                    <Layers size={14} /> Batch Generator
                  </button>
                  <button className="btn btn-secondary btn-sm" onClick={openImportModal} id="btn-import-json">
                    <Upload size={14} /> Import JSON
                  </button>
                  <button className="btn btn-secondary btn-sm" onClick={exportJSON} id="btn-export-json">
                    <Download size={14} /> Export JSON
                  </button>
                </div>
              </div>
            </div>

            <div className="flex items-center justify-between" style={{ borderTop: '1px solid var(--border-light)', paddingTop: 12, marginTop: 8 }}>
              <div className="flex items-center gap-3 text-sm">
                <span>Total Questions: <strong>{editQuestions.length}</strong></span>
                <span>&bull;</span>
                <span>Total Max Marks: <strong style={{ color: 'var(--primary)' }}>{totalPossibleMarks} pts</strong></span>
              </div>
              <button className="btn btn-secondary btn-sm" onClick={addQuestion} id="btn-add-question-top">
                <Plus size={14} /> Add Question
              </button>
            </div>
          </div>

          {/* Question List */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {editQuestions.map((q, qIdx) => (
              <div key={qIdx} className="card" style={{ borderColor: 'var(--border-light)' }}>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="question-id-pill">{q.question_id || `Q${qIdx + 1}`}</span>
                    <span className="text-xs text-muted">Question {qIdx + 1} of {editQuestions.length}</span>
                  </div>
                  <button
                    className="btn btn-ghost btn-icon btn-sm text-danger"
                    onClick={() => removeQuestion(qIdx)}
                    title="Delete Question"
                    id={`btn-del-q-${qIdx}`}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>

                <div className="grid-2 mb-3">
                  <div className="form-group">
                    <label className="form-label">Question Identifier</label>
                    <input
                      className="form-input"
                      value={q.question_id}
                      onChange={e => updateQuestion(qIdx, 'question_id', e.target.value)}
                      placeholder="e.g. Q1, Q2a"
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Max Marks</label>
                    <input
                      className="form-input"
                      type="number"
                      step="0.5"
                      min="0"
                      value={q.max_marks}
                      onChange={e => updateQuestion(qIdx, 'max_marks', parseFloat(e.target.value) || 0)}
                    />
                  </div>
                </div>

                <div className="form-group mb-3">
                  <label className="form-label">Golden / Ideal Model Answer</label>
                  <textarea
                    className="form-textarea"
                    rows={3}
                    value={q.golden_answer || ''}
                    onChange={e => updateQuestion(qIdx, 'golden_answer', e.target.value)}
                    placeholder="Enter the comprehensive reference answer. LLM uses this along with rubrics to evaluate student answers."
                  />
                </div>

                {/* Criteria Sub-section */}
                <div style={{ background: 'var(--bg-elevated)', borderRadius: 8, padding: 14, marginTop: 12 }}>
                  <div className="flex items-center justify-between mb-3">
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
                      Evaluation Criteria & Expected Concepts
                    </span>
                    <button className="btn btn-ghost btn-sm" onClick={() => addCriterion(qIdx)} id={`btn-add-crit-${qIdx}`}>
                      <Plus size={13} /> Add Criterion
                    </button>
                  </div>

                  {(q.criteria || []).map((c, cIdx) => (
                    <div key={cIdx} className="card mb-3" style={{ background: 'var(--bg-card)', padding: 12 }}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-mono text-muted">Criterion #{cIdx + 1}</span>
                        <button
                          className="btn btn-ghost btn-icon btn-sm"
                          onClick={() => removeCriterion(qIdx, cIdx)}
                          title="Remove criterion"
                        >
                          <X size={13} />
                        </button>
                      </div>

                      <div className="grid-2 mb-2">
                        <div className="form-group">
                          <label className="form-label">Criterion Name</label>
                          <input
                            className="form-input"
                            value={c.name}
                            onChange={e => updateCriterion(qIdx, cIdx, 'name', e.target.value)}
                            placeholder="e.g. Definition of Key Concept"
                          />
                        </div>
                        <div className="form-group">
                          <label className="form-label">Marks Allocated</label>
                          <input
                            className="form-input"
                            type="number"
                            step="0.5"
                            min="0"
                            value={c.marks}
                            onChange={e => updateCriterion(qIdx, cIdx, 'marks', parseFloat(e.target.value) || 0)}
                          />
                        </div>
                      </div>

                      <div className="form-group mb-2">
                        <label className="form-label">Teacher Guidance / Rubric Rule</label>
                        <input
                          className="form-input"
                          value={c.guidance || ''}
                          onChange={e => updateCriterion(qIdx, cIdx, 'guidance', e.target.value)}
                          placeholder="e.g. Award full marks only if at least 2 distinct examples are cited."
                        />
                      </div>

                      {/* Expected Concept Chips */}
                      <div className="form-group">
                        <label className="form-label">
                          Key points for the LLM (press Enter to add)
                        </label>
                        <div className="flex flex-wrap items-center gap-1 mb-2">
                          {(c.expected_concepts || []).map((tag, tIdx) => (
                            <span
                              key={tIdx}
                              className="badge badge-info flex items-center gap-1"
                              style={{ padding: '4px 8px' }}
                            >
                              <span>{tag}</span>
                              <X
                                size={12}
                                style={{ cursor: 'pointer' }}
                                onClick={() => removeConceptTag(qIdx, cIdx, tIdx)}
                              />
                            </span>
                          ))}
                        </div>
                        <input
                          className="form-input"
                          placeholder="Type a key point and press Enter (judged by meaning, not exact words)…"
                          onKeyDown={e => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              addConceptTag(qIdx, cIdx, e.target.value)
                              e.target.value = ''
                            }
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Bottom Action Footer */}
          <div className="card mt-4 flex items-center justify-between" style={{ padding: '16px 20px' }}>
            <button className="btn btn-secondary" onClick={addQuestion} id="btn-add-question-bottom">
              <Plus size={16} /> Add Another Question
            </button>
            <div className="flex items-center gap-2">
              <button className="btn btn-ghost" onClick={() => setIsEditing(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving} id="btn-save-bottom">
                <Save size={16} /> {saving ? 'Saving…' : 'Save Configuration'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* JSON Modal */}
      {jsonModalOpen && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: 700 }}>
            <div className="flex items-center justify-between mb-3">
              <h3 style={{ margin: 0 }}>Import / Export JSON Rubric</h3>
              <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setJsonModalOpen(false)}>
                <X size={16} />
              </button>
            </div>
            <p className="text-xs text-muted mb-3">
              Paste your custom rubric JSON containing question identifiers, golden answers, and criteria.
            </p>
            <textarea
              className="form-textarea font-mono text-xs mb-3"
              rows={16}
              value={jsonText}
              onChange={e => setJsonText(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <button className="btn btn-ghost" onClick={() => setJsonModalOpen(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={applyImportedJSON}>
                Apply JSON
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Batch Generator Modal */}
      {batchModalOpen && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: 500 }}>
            <div className="flex items-center justify-between mb-3">
              <h3 style={{ margin: 0 }}>Batch Question Generator</h3>
              <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setBatchModalOpen(false)}>
                <X size={16} />
              </button>
            </div>
            <p className="text-xs text-muted mb-3">
              Quickly generate a large sequence of questions (e.g. 30 questions) with uniform max marks. You can customize details afterwards.
            </p>
            <div className="grid-2 mb-4">
              <div className="form-group">
                <label className="form-label">Number of Questions</label>
                <input
                  className="form-input"
                  type="number"
                  min="1"
                  max="100"
                  value={batchCount}
                  onChange={e => setBatchCount(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Marks per Question</label>
                <input
                  className="form-input"
                  type="number"
                  min="0.5"
                  step="0.5"
                  value={batchMarks}
                  onChange={e => setBatchMarks(e.target.value)}
                />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn btn-ghost" onClick={() => setBatchModalOpen(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={handleBatchGenerate} id="btn-confirm-batch">
                Generate Questions
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
