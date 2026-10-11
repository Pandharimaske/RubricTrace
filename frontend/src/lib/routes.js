/**
 * Single source of truth for URLs.
 * Pages link with `paths.script(id)` instead of hand-building strings.
 */
export const paths = {
  dashboard: '/',
  exams: '/exams',
  exam: (id, tab) => `/exams/${encodeURIComponent(id)}${tab ? `/${tab}` : ''}`,
  students: '/students',
  student: (id) => `/students/${encodeURIComponent(id)}`,
  scripts: '/scripts',
  script: (id) => `/scripts/${encodeURIComponent(id)}`,
  review: '/review',
}

/** The tabs of an exam workspace, in workflow order. */
export const EXAM_STEPS = [
  { key: 'rubric',  label: 'Answer key' },
  { key: 'scripts', label: 'Scripts' },
  { key: 'grade',   label: 'Grade' },
  { key: 'review',  label: 'Review' },
  { key: 'results', label: 'Results' },
]
