import React, { useRef, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { ExternalLink, FileText, Play, Trash2, Upload, X, AlertTriangle } from 'lucide-react'
import JobPanel from '../../components/JobPanel'
import { useToast } from '../../components/Toast'
import { EmptyState, QueryGate, StatusBadge } from '../../components/ui'
import {
  isJobActive, useCancelJob, useDeleteScript, useExamScripts, useStartProcess, useUploadToExam,
} from '../../hooks/queries'
import { paths } from '../../lib/routes'

const ACCEPTED = /\.(pdf|png|jpe?g)$/i
const nameFromFile = (file) => file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim()

export default function ExamScripts() {
  const { exam, job } = useOutletContext()
  const scripts = useExamScripts(exam.exam_id)
  const upload = useUploadToExam(exam.exam_id)
  const startProcess = useStartProcess(exam.exam_id)
  const cancel = useCancelJob(exam.exam_id)
  const toast = useToast()
  const inputRef = useRef(null)

  const [staged, setStaged] = useState([])
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(null)

  const busyJob = isJobActive(job)
  const noQuestions = exam.question_count === 0

  function addFiles(fileList) {
    const files = Array.from(fileList || [])
    const ok = files.filter((f) => ACCEPTED.test(f.name))
    if (ok.length < files.length) toast.error(`Skipped ${files.length - ok.length} file(s): only PDF, PNG and JPEG are supported.`)
    setStaged((current) => {
      const seen = new Set(current.map((s) => s.id))
      const added = ok
        .map((file) => ({ id: `${file.name}:${file.size}:${file.lastModified}`, file, name: nameFromFile(file) }))
        .filter((s) => !seen.has(s.id))
      return [...current, ...added]
    })
  }

  const rename = (id, name) => setStaged((list) => list.map((s) => (s.id === id ? { ...s, name } : s)))
  const unstage = (id) => setStaged((list) => list.filter((s) => s.id !== id))

  function uploadAll() {
    upload.mutate(
      {
        items: staged.map((s) => ({ file: s.file, studentName: s.name.trim() })),
        onProgress: (done, total) => setProgress({ done, total }),
      },
      {
        onSuccess: (outcome) => {
          setProgress(null)
          const failedNames = new Set(outcome.failed.map((f) => f.name))
          setStaged((list) => list.filter((s) => failedNames.has(s.file.name)))
          outcome.failed.forEach((f) => toast.error(`${f.name}: ${f.message}`))
          if (outcome.uploaded) toast.success(`Uploaded ${outcome.uploaded} script${outcome.uploaded > 1 ? 's' : ''}`)
          // Straight on to cutting the papers into questions.
          if (outcome.uploaded && !noQuestions && !busyJob) {
            startProcess.mutate(undefined, { onError: (e) => toast.error(e.message) })
          }
        },
        onError: (e) => { setProgress(null); toast.error(e.message) },
      },
    )
  }

  return (
    <div>
      {noQuestions && (
        <div className="alert alert-warning mb-4">
          <AlertTriangle size={16} />
          <div>
            Define the questions first so we know how many to look for.{' '}
            <Link to={paths.exam(exam.exam_id, 'rubric')} className="btn btn-sm btn-warning" style={{ marginLeft: 8 }}>
              Go to answer key
            </Link>
          </div>
        </div>
      )}

      <JobPanel
        job={job}
        kind="process"
        verb="Reading scripts"
        unit="scripts"
        onCancel={() => cancel.mutate()}
        cancelling={cancel.isPending}
      />

      <div
        className={`upload-zone ${dragging ? 'dragging' : ''}`}
        role="button"
        tabIndex={0}
        aria-label="Choose scripts to upload"
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files) }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click() } }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg"
          onChange={(e) => { addFiles(e.target.files); e.target.value = '' }}
          style={{ display: 'none' }}
        />
        <span className="upload-icon">☁️</span>
        <div className="upload-title">Drop the class's scripts here</div>
        <div className="upload-sub">One PDF (or image) per student · select many at once · click to browse</div>
      </div>

      {staged.length > 0 && (
        <div className="card mt-3">
          <div className="flex items-center justify-between mb-3">
            <h3 className="card-title">Ready to upload ({staged.length})</h3>
            <div className="flex gap-2">
              <button className="btn btn-ghost btn-sm" onClick={() => setStaged([])} disabled={upload.isPending}>Clear</button>
              <button className="btn btn-primary btn-sm" onClick={uploadAll} disabled={upload.isPending}>
                <Upload size={14} />
                {upload.isPending && progress ? ` Uploading ${Math.min(progress.done + 1, progress.total)} of ${progress.total}…` : ` Upload ${staged.length}`}
              </button>
            </div>
          </div>
          <p className="text-xs text-muted mb-2">Student names come from the file names — edit them if needed.</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {staged.map((s) => (
              <div key={s.id} className="flex items-center gap-2">
                <FileText size={14} color="var(--text-muted)" />
                <input
                  className="form-input"
                  style={{ maxWidth: 260 }}
                  value={s.name}
                  onChange={(e) => rename(s.id, e.target.value)}
                  disabled={upload.isPending}
                  aria-label={`Student name for ${s.file.name}`}
                />
                <span className="text-xs text-muted truncate flex-1">{s.file.name} · {(s.file.size / 1024).toFixed(0)} KB</span>
                <button className="btn btn-ghost btn-sm btn-icon" onClick={() => unstage(s.id)} disabled={upload.isPending} aria-label={`Remove ${s.file.name}`}>
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <h3 className="mt-4 mb-3">Uploaded scripts</h3>
      <QueryGate query={scripts} loadingLabel="Loading scripts…">
        {(list) => <ScriptsTable exam={exam} scripts={list} busyJob={busyJob} startProcess={startProcess} />}
      </QueryGate>
    </div>
  )
}

function ScriptsTable({ exam, scripts, busyJob, startProcess }) {
  const del = useDeleteScript(exam.exam_id)
  const toast = useToast()

  if (scripts.length === 0) {
    return <EmptyState icon="📄" title="No scripts uploaded yet" description="Drop the class's PDFs above to begin." />
  }

  const waiting = scripts.filter((s) => s.status === 'uploaded' || s.status === 'error').length

  function remove(script) {
    if (!window.confirm(`Delete ${script.student_name || script.filename}'s script and any marks for it?`)) return
    del.mutate(script.script_id, {
      onSuccess: () => toast.success('Script deleted'),
      onError: (e) => toast.error(e.message),
    })
  }

  return (
    <>
      {waiting > 0 && !busyJob && exam.question_count > 0 && (
        <div className="alert alert-info mb-3 flex items-center justify-between">
          <span>{waiting} script{waiting > 1 ? 's are' : ' is'} waiting to be read.</span>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => startProcess.mutate(undefined, { onError: (e) => toast.error(e.message) })}
            disabled={startProcess.isPending}
          >
            <Play size={14} /> Process {waiting}
          </button>
        </div>
      )}

      <div className="table-wrapper">
        <table>
          <thead>
            <tr><th>Student</th><th>File</th><th>Status</th><th>Questions found</th><th className="text-right">Actions</th></tr>
          </thead>
          <tbody>
            {scripts.map((s) => (
              <tr key={s.script_id}>
                <td style={{ fontWeight: 600 }}>{s.student_name || '—'}</td>
                <td className="text-xs text-muted truncate" style={{ maxWidth: 220 }}>{s.filename}</td>
                <td>
                  <StatusBadge status={s.status} />
                  {s.error && <div className="text-xs" style={{ color: 'var(--danger-light)', maxWidth: 240 }}>{s.error}</div>}
                </td>
                <td className="text-sm">
                  {s.extraction_count > 0 ? `${s.extraction_count} of ${exam.question_count}` : '—'}
                </td>
                <td className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Link to={paths.script(s.script_id)} className="btn btn-ghost btn-sm" aria-label={`Open ${s.student_name || s.filename}`}>
                      <ExternalLink size={13} />
                    </Link>
                    <button className="btn btn-ghost btn-sm btn-icon" onClick={() => remove(s)} disabled={busyJob} aria-label={`Delete ${s.student_name || s.filename}`}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
