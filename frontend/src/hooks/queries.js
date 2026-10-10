import { useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api'

/** All cache keys live here so invalidation is consistent. */
export const keys = {
  health: ['health'],
  stats: ['stats'],
  scripts: ['scripts'],
  script: (id) => ['scripts', id],
  students: ['students'],
  student: (id) => ['students', id],
  reviewQueue: ['review-queue'],
  rubrics: ['rubric-configs'],
  // Everything about one exam nests under ['exams', id, ...] so one
  // invalidation of keys.exam(id) refreshes summary, scripts, results and job.
  exams: ['exams'],
  exam: (id) => ['exams', id],
  examScripts: (id) => ['exams', id, 'scripts'],
  examResults: (id) => ['exams', id, 'results'],
  examJob: (id) => ['exams', id, 'job'],
  evaluatorConfigs: ['evaluator-configs'],
}

export const isJobActive = (job) => job?.status === 'queued' || job?.status === 'running'

// ── Queries ──────────────────────────────────────────────────────────────────

export const useHealth = () =>
  useQuery({
    queryKey: keys.health,
    queryFn: () => api.health(),
    refetchInterval: 15_000,
    retry: false,
  })

// Polled so the sidebar review badge stays current.
export const useStats = () =>
  useQuery({
    queryKey: keys.stats,
    queryFn: () => api.stats(),
    refetchInterval: 30_000,
  })

export const useScripts = () =>
  useQuery({
    queryKey: keys.scripts,
    queryFn: async () => (await api.scripts.list()).scripts ?? [],
  })

export const useScript = (id) =>
  useQuery({
    queryKey: keys.script(id),
    queryFn: () => api.scripts.get(id),
    enabled: Boolean(id),
  })

export const useStudents = () =>
  useQuery({
    queryKey: keys.students,
    queryFn: async () => (await api.students.list()).students ?? [],
  })

export const useStudent = (id) =>
  useQuery({
    queryKey: keys.student(id),
    queryFn: () => api.students.get(id),
    enabled: Boolean(id),
  })

export const useReviewQueue = (examId) =>
  useQuery({
    queryKey: [...keys.reviewQueue, examId ?? 'all'],
    queryFn: async () => {
      const items = (await api.reviewQueue.list(examId)).items ?? []
      // Q2 before Q10, students grouped together
      return [...items].sort(
        (a, b) =>
          (a.student_name || a.filename || '').localeCompare(b.student_name || b.filename || '') ||
          a.script_id.localeCompare(b.script_id) ||
          a.question_id.localeCompare(b.question_id, undefined, { numeric: true }),
      )
    },
  })

export const useExams = () =>
  useQuery({
    queryKey: keys.exams,
    queryFn: async () => (await api.exams.list()).exams ?? [],
  })

export const useExam = (id) =>
  useQuery({
    queryKey: keys.exam(id),
    queryFn: () => api.exams.get(id),
    enabled: Boolean(id),
  })

export const useExamScripts = (id) =>
  useQuery({
    queryKey: keys.examScripts(id),
    queryFn: async () => (await api.exams.scripts(id)).scripts ?? [],
    enabled: Boolean(id),
  })

export const useExamResults = (id) =>
  useQuery({
    queryKey: keys.examResults(id),
    queryFn: () => api.exams.results(id),
    enabled: Boolean(id),
  })

export const useEvaluatorConfigs = () =>
  useQuery({
    queryKey: keys.evaluatorConfigs,
    queryFn: async () => (await api.exams.evaluatorConfigs.list()).evaluator_configs ?? [],
    staleTime: 60_000,
  })

/**
 * The exam's latest background job (segmenting or grading). Polls while it is
 * running and refreshes the exam's data as it progresses and when it finishes.
 */
export function useExamJob(examId) {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: keys.examJob(examId),
    queryFn: async () => (await api.exams.job(examId)).job,
    enabled: Boolean(examId),
    refetchInterval: (q) => (isJobActive(q.state.data) ? 1500 : false),
  })

  const job = query.data
  const wasActive = useRef(false)
  useEffect(() => {
    const active = isJobActive(job)
    if (active || wasActive.current) {
      qc.invalidateQueries({ queryKey: keys.exam(examId) })
      qc.invalidateQueries({ queryKey: keys.reviewQueue })
    }
    if (wasActive.current && !active) {
      qc.invalidateQueries({ queryKey: keys.stats })
    }
    wasActive.current = active
  }, [job?.done, job?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  return { ...query, job }
}

// ── Mutations ────────────────────────────────────────────────────────────────

function useInvalidate() {
  const qc = useQueryClient()
  return (...queryKeys) =>
    Promise.all(queryKeys.map((queryKey) => qc.invalidateQueries({ queryKey })))
}

export function useCreateStudent() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (body) => api.students.create(body),
    onSuccess: () => invalidate(keys.students, keys.stats),
  })
}

export function useCreateExam() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (name) => api.exams.create({ name }),
    onSuccess: () => invalidate(keys.exams),
  })
}

export function useDeleteExam() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (examId) => api.exams.remove(examId),
    onSuccess: () => invalidate(keys.exams, keys.scripts, keys.students, keys.stats),
  })
}

export function useSaveExam(examId) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body) => api.exams.update(examId, body),
    onSuccess: (exam) => {
      qc.setQueryData(keys.exam(examId), exam)
      qc.invalidateQueries({ queryKey: keys.exams })
    },
  })
}

/** Upload several student scripts into an exam, one after another. */
export function useUploadToExam(examId) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async ({ items, onProgress }) => {
      const outcome = { uploaded: 0, failed: [] }
      for (let i = 0; i < items.length; i++) {
        const { file, studentName, studentId } = items[i]
        onProgress?.(i, items.length)
        const fd = new FormData()
        fd.append('file', file)
        fd.append('exam_id', examId)
        if (studentName) fd.append('student_name', studentName)
        if (studentId) fd.append('student_id', studentId)
        try {
          await api.scripts.upload(fd)
          outcome.uploaded += 1
        } catch (e) {
          outcome.failed.push({ name: file.name, message: e.message })
        }
      }
      onProgress?.(items.length, items.length)
      return outcome
    },
    onSettled: () => invalidate(keys.exam(examId), keys.scripts, keys.students, keys.stats),
  })
}

export function useDeleteScript(examId) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (scriptId) => api.scripts.remove(scriptId),
    onSuccess: () => invalidate(keys.exam(examId), keys.scripts, keys.students, keys.stats),
  })
}

function useStartJob(examId, start) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: start,
    onSuccess: ({ job }) => qc.setQueryData(keys.examJob(examId), job),
  })
}

export const useStartProcess = (examId) => useStartJob(examId, () => api.exams.process(examId))
export const useStartGrade = (examId) =>
  useStartJob(examId, ({ regrade = false } = {}) => api.exams.grade(examId, { regrade }))

export function useCancelJob(examId) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: () => api.exams.cancelJob(examId),
    onSuccess: () => invalidate(keys.examJob(examId)),
  })
}

/**
 * Upload then process one script in a single request. Used by the standalone
 * Upload page; exams use useUploadToExam + a background process job instead.
 * If upload succeeds but processing fails, the script still exists, so we
 * return `processError` instead of throwing.
 */
export function useUploadScript() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async ({ file, studentId, studentName, onStage }) => {
      const fd = new FormData()
      fd.append('file', file)
      if (studentId) fd.append('student_id', studentId)
      if (studentName) fd.append('student_name', studentName)

      onStage?.('uploading')
      const upload = await api.scripts.upload(fd)

      onStage?.('processing')
      let process = null
      let processError = null
      try {
        process = await api.scripts.process(upload.script_id)
      } catch (e) {
        processError = e.message
      }
      const questionIds = Object.keys(process?.extraction?.questions || {})
      return { upload, process, processError, questionIds }
    },
    onSettled: () => invalidate(keys.scripts, keys.students, keys.stats),
  })
}

export function useOverrideScore() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: ({ scriptId, questionId, awardedMarks, reason }) =>
      api.scripts.override(scriptId, {
        question_id: questionId,
        awarded_marks: awardedMarks,
        reason,
      }),
    onSuccess: () => invalidate(keys.reviewQueue, keys.stats, keys.scripts, keys.exams),
  })
}
