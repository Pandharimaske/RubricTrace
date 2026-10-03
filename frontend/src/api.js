/**
 * RubricTrace API client
 */

const BASE = '/api'

function messageFrom(detail, fallback) {
  if (typeof detail === 'string') return detail
  // FastAPI validation errors arrive as a list of { loc, msg }
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const where = (d.loc || []).filter((p) => p !== 'body').join(' › ')
        const msg = String(d.msg || '').replace(/^Value error, /, '')
        return where && !msg.includes(where) ? `${where}: ${msg}` : msg
      })
      .join('; ')
  }
  return fallback
}

async function request(method, path, body, isFormData = false) {
  const opts = {
    method,
    headers: isFormData ? {} : { 'Content-Type': 'application/json' },
  }
  if (body !== undefined && body !== null) {
    opts.body = isFormData ? body : JSON.stringify(body)
  }

  let res
  try {
    res = await fetch(`${BASE}${path}`, opts)
  } catch {
    throw new Error("Can't reach the server. Is the backend running?")
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(messageFrom(err.detail, res.statusText || `HTTP ${res.status}`))
  }
  return res.json()
}

const get  = (path)                   => request('GET',    path)
const post = (path, body, isFormData) => request('POST',   path, body, isFormData)
const put  = (path, body)             => request('PUT',    path, body)
const del  = (path)                   => request('DELETE', path)

export const api = {
  // Health
  health: () => get('/health'),

  // Stats
  stats: () => get('/stats'),

  // Exams: one answer key per exam, scripts belong to it
  exams: {
    list:        ()         => get('/exams'),
    create:      (body)     => post('/exams', body),
    get:         (id)       => get(`/exams/${id}`),
    update:      (id, body) => put(`/exams/${id}`, body),
    remove:      (id)       => del(`/exams/${id}`),
    scripts:     (id)       => get(`/exams/${id}/scripts`),
    results:     (id)       => get(`/exams/${id}/results`),
    exportUrl:   (id)       => `${BASE}/exams/${id}/export.csv`,
    process:     (id)       => post(`/exams/${id}/process`, {}),
    grade:       (id, body) => post(`/exams/${id}/grade`, body || {}),
    job:         (id)       => get(`/exams/${id}/job`),
    cancelJob:   (id)       => post(`/exams/${id}/job/cancel`, {}),
      evaluatorConfigs: {
        list:   ()       => get('/exams/evaluator-configs'),
        create: (body)   => post('/exams/evaluator-configs', body),
      },
  },

  // Students
  students: {
    list:   ()      => get('/students'),
    create: (body)  => post('/students', body),
    get:    (id)    => get(`/students/${id}`),
  },

  // Scripts
  scripts: {
    list:      ()             => get('/scripts'),
    get:       (id)           => get(`/scripts/${id}`),
    upload:    (formData)     => post('/scripts/upload', formData, true),
    remove:    (id)           => del(`/scripts/${id}`),
    process:   (id)           => post(`/scripts/${id}/process`, {}),
    extractions:(id)           => get(`/scripts/${id}/extractions`),
    pageImageUrl: (id, pageNumber) => `${BASE}/scripts/${id}/pages/${pageNumber}/image`,
    evaluate:  (id, body)     => post(`/scripts/${id}/evaluate`, body),
    evaluation:(id)           => get(`/scripts/${id}/evaluation`),
    override:  (id, body)     => post(`/scripts/${id}/override`, body),
  },

  // Rubric configs (reusable templates)
  rubricConfigs: {
    list:   ()     => get('/rubric-configs'),
    save:   (body) => post('/rubric-configs', body),
    get:    (id)   => get(`/rubric-configs/${id}`),
  },

  // Review Queue (optionally limited to one exam)
  reviewQueue: {
    list: (examId) => get(examId ? `/review-queue?exam_id=${encodeURIComponent(examId)}` : '/review-queue'),
  },
}
