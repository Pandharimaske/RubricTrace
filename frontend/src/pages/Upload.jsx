import React, { useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Upload, CheckCircle, AlertCircle, AlertTriangle, ChevronRight } from 'lucide-react'
import { useUploadScript } from '../hooks/queries'
import { paths } from '../lib/routes'
import { PageHeader, SectionLabel } from '../components/ui'

const ACCEPTED_TYPES = ['application/pdf', 'image/png', 'image/jpeg']
const ACCEPTED_EXT = /\.(pdf|png|jpe?g)$/i

const STEPS = [
  ['1', 'PDF → page images', 'Each page is rendered at high resolution.'],
  ['2', 'Question detection', 'Visual regions are detected and cropped per question.'],
  ['3', 'Text extraction', 'OCR + VLM extract handwritten or typed answers.'],
  ['4', 'Grade with rubric', 'Apply your rubric to score every question.'],
]

export default function UploadPage() {
  const [searchParams] = useSearchParams()
  const upload = useUploadScript()
  const inputRef = useRef(null)

  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState(null)
  const [fileError, setFileError] = useState(null)
  const [studentName, setStudentName] = useState('')
  // /upload?student=ID pre-fills the student (linked from the student page)
  const [studentId, setStudentId] = useState(searchParams.get('student') || '')
  const [stage, setStage] = useState(null) // 'uploading' | 'processing'

  const busy = upload.isPending
  const result = upload.data

  function pickFile(f) {
    if (!f) return
    if (!ACCEPTED_TYPES.includes(f.type) && !ACCEPTED_EXT.test(f.name)) {
      setFileError('Only PDF, PNG, or JPEG files are supported.')
      return
    }
    setFile(f)
    setFileError(null)
    upload.reset()
  }

  function submit() {
    if (!file || busy) return
    upload.mutate({
      file,
      studentId: studentId.trim(),
      studentName: studentName.trim(),
      onStage: setStage,
    })
  }

  function reset() {
    setFile(null)
    setStudentName('')
    setStudentId('')
    setFileError(null)
    upload.reset()
  }

  const error = fileError || (upload.isError ? upload.error.message : null)

  return (
    <div>
      <PageHeader
        title="Upload Student Script"
        description="Upload a scanned PDF or image. The system will extract and segment each question automatically."
      />

      <div className="grid-2" style={{ alignItems: 'start' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div
            className={`upload-zone ${dragging ? 'dragging' : ''}`}
            role="button"
            tabIndex={0}
            aria-label="Choose a file to upload"
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); pickFile(e.dataTransfer.files[0]) }}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click() }
            }}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".pdf,.png,.jpg,.jpeg"
              onChange={(e) => pickFile(e.target.files[0])}
              style={{ display: 'none' }}
            />
            <span className="upload-icon">{file ? '📄' : '☁️'}</span>
            {file ? (
              <>
                <div className="upload-title">{file.name}</div>
                <div className="upload-sub">{(file.size / 1024).toFixed(1)} KB — click to change</div>
              </>
            ) : (
              <>
                <div className="upload-title">Drop PDF or image here</div>
                <div className="upload-sub">Supports PDF, PNG, JPEG · click to browse</div>
              </>
            )}
          </div>

          <div className="card">
            <h3 className="card-title mb-3">Student Information</h3>
            <div className="form-group">
              <label className="form-label" htmlFor="student-name">Student Name (optional)</label>
              <input
                id="student-name"
                className="form-input"
                placeholder="e.g. Priya Sharma"
                value={studentName}
                onChange={(e) => setStudentName(e.target.value)}
                disabled={busy}
              />
            </div>
            <div className="form-group" style={{ marginBottom: 0 }}>
              <label className="form-label" htmlFor="student-id">Student ID (optional)</label>
              <input
                id="student-id"
                className="form-input"
                placeholder="e.g. STU001 (auto-generated if blank)"
                value={studentId}
                onChange={(e) => setStudentId(e.target.value)}
                disabled={busy}
              />
            </div>
          </div>

          {error && (
            <div className="alert alert-danger" role="alert">
              <AlertCircle size={16} /> {error}
            </div>
          )}

          <button className="btn btn-primary btn-lg" onClick={submit} disabled={!file || busy}>
            {busy ? (
              <><span className="spinner" /> {stage === 'processing' ? 'Extracting questions…' : 'Uploading…'}</>
            ) : (
              <><Upload size={18} /> Upload &amp; Process</>
            )}
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {busy && (
            <div className="card text-center" style={{ padding: 40 }} role="status">
              <div className="spinner spinner-lg" style={{ margin: '0 auto 16px' }} />
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
                {stage === 'processing' ? 'Extracting question regions…' : 'Uploading file…'}
              </div>
              <p className="text-sm">This may take a moment for multi-page PDFs.</p>
            </div>
          )}

          {result && !busy && <ResultCard result={result} onReset={reset} />}
          {!result && !busy && <NextSteps />}
        </div>
      </div>
    </div>
  )
}

function ResultCard({ result, onReset }) {
  const { upload, process, processError, questionIds } = result
  const scriptHref = paths.script(upload.script_id)

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-4">
        {processError
          ? <AlertTriangle size={20} color="var(--warning-light)" />
          : <CheckCircle size={20} color="var(--success-light)" />}
        <h3 className="card-title">
          {processError ? 'Uploaded — processing needs a retry' : 'Processing Complete'}
        </h3>
      </div>

      {processError ? (
        <div className="alert alert-warning">
          <AlertTriangle size={16} />
          <div>
            The file was saved but question extraction failed: {processError}. Open the script to
            retry segmentation.
          </div>
        </div>
      ) : (
        <>
          <div className="stats-grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 16 }}>
            <div className="stat-card" style={{ '--accent': '#10b981' }}>
              <div className="stat-label">Pages</div>
              <div className="stat-value">{process?.page_count ?? '—'}</div>
            </div>
            <div className="stat-card" style={{ '--accent': '#6366f1' }}>
              <div className="stat-label">Questions Found</div>
              <div className="stat-value">{questionIds.length}</div>
            </div>
          </div>

          {process?.extraction?.method && (
            <div className="mb-4">
              <SectionLabel>Extraction method</SectionLabel>
              <span className="badge badge-info">{process.extraction.method}</span>
            </div>
          )}

          {questionIds.length > 0 && (
            <div className="mb-4">
              <SectionLabel>Detected questions</SectionLabel>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {questionIds.map((qid) => (
                  <span key={qid} className="badge badge-primary">{qid}</span>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <div className="divider" />

      <div style={{ display: 'flex', gap: 8 }}>
        <Link to={scriptHref} className="btn btn-primary flex-1" style={{ justifyContent: 'center' }}>
          {processError ? 'Open Script' : 'Grade This Script'} <ChevronRight size={16} />
        </Link>
        <button className="btn btn-secondary" onClick={onReset}>Upload Another</button>
      </div>
    </div>
  )
}

function NextSteps() {
  return (
    <div className="card" style={{ padding: 32 }}>
      <h3 className="mb-3" style={{ textAlign: 'center' }}>What happens next?</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {STEPS.map(([n, title, desc]) => (
          <div key={n} className="flex gap-3">
            <div
              style={{
                width: 24, height: 24, borderRadius: '50%',
                background: 'var(--primary-glow)', border: '1px solid rgba(99,102,241,0.3)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '0.72rem', fontWeight: 700, color: 'var(--primary-light)',
                flexShrink: 0, marginTop: 2,
              }}
            >
              {n}
            </div>
            <div>
              <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>{title}</div>
              <div className="text-xs text-muted">{desc}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
