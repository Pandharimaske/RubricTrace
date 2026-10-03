/** Where an exam is in the workflow, derived from its summary counts. */

export function stepStates(exam) {
  const keyReady = exam.question_count > 0 && exam.answer_key_complete
  const scriptsReady = exam.script_count > 0 && exam.processed_count === exam.script_count
  const graded = exam.processed_count > 0 && exam.graded_count >= exam.processed_count
  return {
    rubric:  keyReady ? 'done' : 'todo',
    scripts: scriptsReady ? 'done' : 'todo',
    grade:   graded ? 'done' : 'todo',
    review:  exam.graded_count > 0 && exam.review_count === 0 ? 'done' : 'todo',
    results: exam.graded_count > 0 ? 'done' : 'todo',
  }
}

/** The step the teacher should do next. */
export function nextStep(exam) {
  const s = stepStates(exam)
  if (s.rubric !== 'done') return 'rubric'
  if (s.scripts !== 'done') return 'scripts'
  if (s.grade !== 'done') return 'grade'
  if (exam.review_count > 0) return 'review'
  return 'results'
}

export const NEXT_LABEL = {
  rubric:  'Set up the answer key',
  scripts: 'Upload & process scripts',
  grade:   'Grade the class',
  review:  'Review flagged answers',
  results: 'View results',
}
