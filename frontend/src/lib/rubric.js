/**
 * Answer-key helpers: draft <-> API payload, validation, JSON import/export.
 * The draft keeps numbers as typed (so "2." is allowed while typing) and
 * concepts as one comma/newline separated string; the payload is what the API wants.
 */

let counter = 0
const nextKey = () => `k${++counter}`

export const blankCriterion = () => ({
  _key: nextKey(), name: '', marks: 1, expected_concepts: '', guidance: '',
})

export const blankOption = () => ({
  _key: nextKey(), option_key: '', option_text: '', is_correct: false,
})

export const blankQuestion = (question_id = '', max_marks = 5) => ({
  _key: nextKey(), question_id, question_text: '', question_type: 'short_answer',
  max_marks, golden_answer: '', review_confidence_threshold: null, evaluator_config_id: null,
  options: [], criteria: [],
})

export const splitConcepts = (text) =>
  String(text || '').split(/[,\n]/).map((s) => s.trim()).filter(Boolean)

export function toDraft(questions = []) {
  return questions.map((q) => ({
    _key: nextKey(),
    question_id: q.question_id ?? '',
    question_text: q.question_text ?? '',
    question_type: q.question_type ?? 'short_answer',
    max_marks: q.max_marks ?? 1,
    golden_answer: q.golden_answer ?? '',
    review_confidence_threshold: q.review_confidence_threshold ?? null,
    evaluator_config_id: q.evaluator_config_id ?? null,
    options: (q.options || []).map((option) => ({ _key: nextKey(), ...option })),
    criteria: (q.criteria || []).map((c) => ({
      _key: nextKey(),
      name: c.name ?? c.criterion ?? '',
      marks: c.marks ?? c.points ?? 1,
      expected_concepts: Array.isArray(c.expected_concepts)
        ? c.expected_concepts.join(', ')
        : String(c.expected_concepts || ''),
      guidance: c.guidance ?? '',
    })),
  }))
}

export const toPayload = (draft) =>
  draft.map((q) => {
    const isObjective = q.question_type === 'mcq' || q.question_type === 'true_false'
    return {
      question_id: q.question_id.trim(),
      question_text: q.question_text.trim(),
      question_type: q.question_type,
      golden_answer: q.golden_answer.trim(),
      max_marks: Number(q.max_marks),
      review_confidence_threshold: q.review_confidence_threshold === '' || q.review_confidence_threshold == null
        ? null : Number(q.review_confidence_threshold),
      evaluator_config_id: q.evaluator_config_id || null,
      options: isObjective ? (q.options || []).map((option, index) => ({
        option_key: option.option_key.trim() || String.fromCharCode(65 + index),
        option_text: option.option_text.trim(),
        is_correct: Boolean(option.is_correct),
        display_order: index,
      })) : [],
      criteria: !isObjective ? (q.criteria || []).map((c) => ({
        name: c.name.trim(),
        marks: Number(c.marks),
        expected_concepts: splitConcepts(c.expected_concepts),
        guidance: c.guidance.trim(),
      })) : [],
    }
  })

const round2 = (n) => Math.round(n * 100) / 100
export const criteriaTotal = (q) => round2(q.criteria.reduce((s, c) => s + (Number(c.marks) || 0), 0))
export const totalMarks = (draft) => round2(draft.reduce((s, q) => s + (Number(q.max_marks) || 0), 0))

/**
 * Returns { errors, warnings } keyed by question _key.
 * Errors block saving (the API would reject them); warnings only block grading.
 */
export function validateDraft(draft) {
  const errors = {}
  const warnings = {}
  const seen = new Map()
  const add = (bag, q, msg) => { (bag[q._key] ||= []).push(msg) }

  for (const q of draft) {
    const id = q.question_id.trim()
    if (!id) add(errors, q, 'Question needs an ID (e.g. Q1).')
    else if (seen.has(id)) {
      add(errors, q, `Duplicate ID "${id}".`)
      add(errors, seen.get(id), `Duplicate ID "${id}".`)
    } else seen.set(id, q)

    if (!(Number(q.max_marks) > 0)) add(errors, q, 'Max marks must be more than 0.')
    if (!q.question_text.trim()) add(warnings, q, 'No question text yet (needed so the grader knows what was asked).')

    if (!['mcq', 'true_false', 'short_answer', 'long_answer'].includes(q.question_type)) {
      add(errors, q, 'Choose a valid question type.')
    }
    if (q.question_type === 'mcq') {
      if (q.options.length < 2) add(errors, q, 'MCQ questions need at least two options.')
      if (q.options.filter((option) => option.is_correct).length !== 1) add(errors, q, 'MCQ questions need exactly one correct option.')
      if (q.options.some((option) => !option.option_text.trim())) add(errors, q, 'Every MCQ option needs text.')
    }
    if (q.question_type === 'true_false' && q.options.length && q.options.filter((option) => option.is_correct).length !== 1) {
      add(errors, q, 'True/false questions need exactly one correct option.')
    }
    if (['short_answer', 'long_answer'].includes(q.question_type) && !q.criteria.length) {
      add(errors, q, 'Open-ended questions need at least one rubric criterion.')
    }

    q.criteria.forEach((c, i) => {
      if (!c.name.trim()) add(errors, q, `Criterion ${i + 1} needs a name.`)
      if (!(Number(c.marks) > 0)) add(errors, q, `Criterion ${i + 1} marks must be more than 0.`)
    })
    if (q.criteria.length && Math.abs(criteriaTotal(q) - Number(q.max_marks)) > 0.01) {
      add(errors, q, `Criteria add up to ${criteriaTotal(q)}, but max marks is ${q.max_marks}.`)
    }
    if (!q.golden_answer.trim()) add(warnings, q, 'No reference answer yet (needed before grading).')
  }
  return { errors, warnings }
}

export const hasErrors = (result) => Object.keys(result.errors).length > 0

/** Split a question's marks evenly across its criteria. */
export function splitEvenly(q) {
  if (!q.criteria.length) return q
  const each = round2(Number(q.max_marks) / q.criteria.length)
  const criteria = q.criteria.map((c) => ({ ...c, marks: each }))
  // put any rounding remainder on the last criterion so the sum is exact
  const drift = round2(Number(q.max_marks) - each * criteria.length)
  criteria[criteria.length - 1].marks = round2(each + drift)
  return { ...q, criteria }
}

// ── Import / export ──────────────────────────────────────────────────────────

export function parseImport(text) {
  let data
  try {
    data = typeof text === 'string' ? JSON.parse(text) : text
  } catch {
    throw new Error("That file isn't valid JSON.")
  }

  let list = []
  let threshold = null
  let examTitle = null

  if (data && typeof data === 'object') {
    if (data.review_confidence_threshold != null) {
      threshold = Number(data.review_confidence_threshold)
    }
    if (data.exam_title || data.name || data.title) {
      examTitle = String(data.exam_title || data.name || data.title)
    }
  }

  if (Array.isArray(data)) {
    list = data
  } else if (Array.isArray(data?.questions)) {
    list = data.questions
  } else if (Array.isArray(data?.sections)) {
    for (const section of data.sections) {
      const sectionType = section.type || section.question_type || ''
      const sectionMarks = section.marks_per_question
      for (const q of (section.questions || [])) {
        list.push({
          ...q,
          _section_type: sectionType,
          _section_marks: sectionMarks,
        })
      }
    }
  } else if (data && typeof data === 'object') {
    const values = Object.values(data).filter(
      (v) => v && typeof v === 'object' && ('question' in v || 'question_text' in v || 'rubric' in v || 'options' in v)
    )
    if (values.length > 0) {
      list = Object.entries(data).map(([k, v]) => ({ question_id: k, ...v }))
    }
  }

  if (!Array.isArray(list) || list.length === 0) {
    throw new Error('Expected a list of questions, or an object with "questions" or "sections".')
  }

  const normalizedQuestions = list.map((q, i) => {
    let qid = q.question_id ?? q.id
    if (qid == null || qid === '') {
      qid = `Q${i + 1}`
    } else {
      const s = String(qid).trim()
      qid = /^\d+$/.test(s) ? `Q${s}` : s
    }

    const questionText = String(q.question_text ?? q.question ?? q.prompt ?? '')
    const goldenAnswer = String(q.golden_answer ?? q.correct_answer ?? q.model_answer ?? q.answer ?? '')

    let rawType = String(q.question_type ?? q.type ?? q._section_type ?? '').trim().toLowerCase().replace(/[-_\s]/g, '')
    let questionType = 'short_answer'
    if (rawType.includes('mcq') || rawType.includes('multiplechoice')) {
      questionType = 'mcq'
    } else if (rawType.includes('truefalse') || rawType.includes('tf')) {
      questionType = 'true_false'
    } else if (rawType.includes('long')) {
      questionType = 'long_answer'
    } else if (rawType.includes('short')) {
      questionType = 'short_answer'
    } else if (q.options && Object.keys(q.options).length > 0) {
      questionType = 'mcq'
    }

    const maxMarks = Number(q.max_marks ?? q.marks ?? q.points ?? q._section_marks ?? 1) || 1

    let options = []
    if (questionType === 'mcq' || questionType === 'true_false') {
      const correctKey = String(q.correct_answer ?? q.golden_answer ?? '').trim().toUpperCase()
      if (Array.isArray(q.options)) {
        options = q.options.map((opt, idx) => {
          if (typeof opt === 'string') {
            const key = String.fromCharCode(65 + idx)
            return {
              option_key: key,
              option_text: opt.trim(),
              is_correct: key === correctKey,
              display_order: idx,
            }
          }
          const key = String(opt.option_key ?? opt.key ?? String.fromCharCode(65 + idx)).trim()
          return {
            option_key: key,
            option_text: String(opt.option_text ?? opt.text ?? opt.label ?? '').trim(),
            is_correct: Boolean(opt.is_correct ?? (key.toUpperCase() === correctKey)),
            display_order: opt.display_order ?? idx,
          }
        })
      } else if (q.options && typeof q.options === 'object') {
        options = Object.entries(q.options).map(([key, val], idx) => {
          const text = typeof val === 'string' ? val : (val?.text ?? val?.option_text ?? String(val ?? ''))
          const optKey = String(key).trim()
          return {
            option_key: optKey,
            option_text: text.trim(),
            is_correct: optKey.toUpperCase() === correctKey || Boolean(val?.is_correct),
            display_order: idx,
          }
        })
      }

      if (questionType === 'true_false' && options.length === 0) {
        const cLower = goldenAnswer.toLowerCase()
        options = [
          { option_key: 'A', option_text: 'True', is_correct: cLower === 'true' || cLower === 'a' || cLower === 't', display_order: 0 },
          { option_key: 'B', option_text: 'False', is_correct: cLower === 'false' || cLower === 'b' || cLower === 'f', display_order: 1 },
        ]
      }
    }

    let criteria = []
    if (questionType !== 'mcq' && questionType !== 'true_false') {
      const rawCriteria = q.criteria || q.rubric || []
      criteria = rawCriteria.map((c, idx) => {
        let name = String(c.name || '').trim()
        let guidance = String(c.guidance || '').trim()
        const text = String(c.criterion || c.description || '').trim()

        if (!name) {
          if (text) {
            if (text.includes(' — ')) {
              const parts = text.split(' — ')
              name = parts[0].trim()
              if (!guidance) guidance = parts.slice(1).join(' — ').trim()
            } else if (text.includes(' - ')) {
              const parts = text.split(' - ')
              name = parts[0].trim()
              if (!guidance) guidance = parts.slice(1).join(' - ').trim()
            } else if (text.length > 80) {
              name = text.slice(0, 80).trim() + '...'
              if (!guidance) guidance = text
            } else {
              name = text
            }
          } else {
            name = `Criterion ${idx + 1}`
          }
        }
        if (!guidance && text && text !== name) {
          guidance = text
        }

        return {
          name,
          marks: Number(c.marks ?? c.points ?? 1) || 1,
          expected_concepts: Array.isArray(c.expected_concepts)
            ? c.expected_concepts
            : (c.expected_concepts ? splitConcepts(c.expected_concepts) : []),
          guidance,
        }
      })

      if (criteria.length === 0) {
        criteria = [{
          name: 'Correctness',
          marks: maxMarks,
          expected_concepts: [],
          guidance: '',
        }]
      }
    }

    return {
      question_id: qid,
      question_text: questionText,
      question_type: questionType,
      golden_answer: goldenAnswer,
      max_marks: maxMarks,
      options,
      criteria,
      review_confidence_threshold: q.review_confidence_threshold != null ? Number(q.review_confidence_threshold) : null,
      evaluator_config_id: q.evaluator_config_id || null,
    }
  })

  const draft = toDraft(normalizedQuestions)
  if (threshold != null || examTitle != null) {
    draft.metadata = {
      review_confidence_threshold: threshold,
      exam_title: examTitle,
    }
  }
  return draft
}

export function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export const TEMPLATE = {
  questions: [
    {
      question_id: 'Q1',
      question_text: 'What is the question the student was asked?',
      max_marks: 5,
      golden_answer: 'Reference answer the teacher expects…',
      criteria: [
        { name: 'Definition', marks: 2, expected_concepts: ['key idea one', 'key idea two'], guidance: 'Award full marks only if both ideas appear.' },
        { name: 'Example', marks: 3, expected_concepts: ['worked example'], guidance: '' },
      ],
    },
  ],
}
