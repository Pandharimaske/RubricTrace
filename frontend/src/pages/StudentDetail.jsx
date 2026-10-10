import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { FileText, ChevronRight, Upload, Calendar } from 'lucide-react'
import { useStudent } from '../hooks/queries'
import { paths } from '../lib/routes'
import { PageHeader, QueryGate, StatusBadge, EmptyState } from '../components/ui'

export default function StudentDetail() {
  const { studentId } = useParams()
  const query = useStudent(studentId)

  return (
    <div>
      <PageHeader
        title="Student"
        backTo={paths.students}
        backLabel="All Students"
      />
      <QueryGate query={query} loadingLabel="Loading student record…">
        {({ student, scripts = [] }) => <StudentBody student={student} scripts={scripts} />}
      </QueryGate>
    </div>
  )
}

function StudentBody({ student, scripts }) {
  const uploadHref = paths.upload(student.student_id)

  return (
    <>
      <div className="card mb-4" style={{ padding: 24 }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              style={{
                width: 52, height: 52, borderRadius: '50%',
                background: 'linear-gradient(135deg, var(--primary), #818cf8)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: '#fff', fontSize: 22, fontWeight: 700,
              }}
              aria-hidden="true"
            >
              {(student.name || student.student_id).charAt(0).toUpperCase()}
            </div>
            <div>
              <h2 style={{ margin: 0 }}>{student.name || 'Unnamed Student'}</h2>
              <div className="flex items-center gap-3 text-xs text-muted mt-1">
                <span>ID: <strong className="font-mono text-primary">{student.student_id}</strong></span>
                <span>&bull;</span>
                <span className="flex items-center gap-1">
                  <Calendar size={12} /> Registered {student.created_at?.slice(0, 10)}
                </span>
              </div>
            </div>
          </div>
          <Link to={uploadHref} className="btn btn-primary">
            <Upload size={14} /> Upload Script
          </Link>
        </div>
      </div>

      <h3 className="mb-3">Scripts ({scripts.length})</h3>

      {scripts.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<FileText size={36} />}
            title="No scripts uploaded yet"
            description="Upload a scanned PDF for this student to start segmentation and grading."
            action={
              <Link to={uploadHref} className="btn btn-primary">
                <Upload size={14} /> Upload First Script
              </Link>
            }
          />
        </div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>File / Script</th>
                <th>Status</th>
                <th>Pages</th>
                <th>Uploaded</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {scripts.map((s) => (
                <tr key={s.script_id}>
                  <td>
                    <div className="flex items-center gap-2">
                      <FileText size={16} color="var(--primary)" />
                      <div>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{s.filename}</div>
                        <div className="font-mono text-xs text-muted">{s.script_id}</div>
                      </div>
                    </div>
                  </td>
                  <td><StatusBadge status={s.status} /></td>
                  <td className="text-sm">{s.page_count ?? '—'}</td>
                  <td className="text-xs text-muted">{s.created_at?.slice(0, 16)}</td>
                  <td className="text-right">
                    <Link to={paths.script(s.script_id)} className="btn btn-primary btn-sm">
                      Open &amp; Grade <ChevronRight size={14} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
