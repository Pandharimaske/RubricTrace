/**
 * Single source of truth for URLs.
 * Pages link with `paths.script(id)` instead of hand-building strings.
 */
export const paths = {
  dashboard: '/',
  exams: '/exams',
  exam: (id, tab) => `/exams/${encodeURIComponent(id)}${tab ? `/${tab}` : ''}`,
  upload: (studentId) =>
    studentId ? `/upload?student=${encodeURIComponent(studentId)}` : '/upload',
  students: '/students',
  student: (id) => `/students/${encodeURIComponent(id)}`,
  scripts: '/scripts',
  script: (id) => `/scripts/${encodeURIComponent(id)}`,
  review: '/review',
  rubrics: '/rubrics',
}

/** The tabs of an exam workspace, in workflow order. */
export const EXAM_STEPS = [
  { key: 'rubric',  label: 'Answer key' },
  { key: 'scripts', label: 'Scripts' },
  { key: 'grade',   label: 'Grade' },
  { key: 'review',  label: 'Review' },
  { key: 'results', label: 'Results' },
]

/**
 * Maps the old `navigate('script-detail', { scriptId })` calls to real URLs.
 * Only used by pages that have not been migrated to the router yet
 * (ScriptDetail, RubricConfigs). Delete once they are.
 */
export const legacyTargets = {
  dashboard: () => paths.dashboard,
  upload: (p) => paths.upload(p.studentId),
  students: () => paths.students,
  'students-detail': (p) => paths.student(p.studentId),
  scripts: () => paths.scripts,
  'script-detail': (p) => paths.script(p.scriptId),
  review: () => paths.review,
  rubrics: () => paths.rubrics,
}
