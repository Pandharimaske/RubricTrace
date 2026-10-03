import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { FileText, ChevronRight, Search, Upload } from 'lucide-react'
import { useScripts } from '../hooks/queries'
import { paths } from '../lib/routes'
import { PageHeader, QueryGate, StatusBadge, EmptyState } from '../components/ui'

const STATUS_OPTIONS = ['uploaded', 'processed', 'graded', 'needs_review', 'teacher_approved']

export default function ScriptsPage() {
  const scripts = useScripts()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')

  return (
    <div>
      <PageHeader
        title="Scripts"
        description="All uploaded student answer scripts."
        actions={
          <Link to={paths.upload()} className="btn btn-primary">
            <Upload size={16} /> Upload New
          </Link>
        }
      />

      <div className="card mb-4 search-bar">
        <Search size={16} color="var(--text-muted)" />
        <input
          className="form-input"
          placeholder="Search by filename or student…"
          aria-label="Search scripts"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="form-select"
          style={{ width: 'auto' }}
          aria-label="Filter by status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">All statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>{s.replace('_', ' ')}</option>
          ))}
        </select>
      </div>

      <QueryGate query={scripts} loadingLabel="Loading scripts…">
        {(data) => <ScriptsTable scripts={data} search={search} status={status} />}
      </QueryGate>
    </div>
  )
}

function ScriptsTable({ scripts, search, status }) {
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return [...scripts]
      .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
      .filter((s) => !status || s.status === status)
      .filter(
        (s) =>
          !q ||
          [s.filename, s.student_id, s.student_name].some((v) => v?.toLowerCase().includes(q)),
      )
  }, [scripts, search, status])

  if (rows.length === 0) {
    const filtered = search || status
    return (
      <EmptyState
        icon={<FileText size={48} />}
        title={filtered ? 'No matching scripts' : 'No scripts uploaded yet'}
        description={filtered ? 'Try a different search or filter.' : 'Upload a PDF to begin grading.'}
      />
    )
  }

  return (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Filename</th>
            <th>Student</th>
            <th>Pages</th>
            <th>Status</th>
            <th>Uploaded</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.script_id}>
              <td style={{ fontWeight: 600 }}>
                <Link to={paths.script(s.script_id)} className="flex items-center gap-2" style={{ color: 'var(--text-primary)', textDecoration: 'none' }}>
                  <FileText size={14} color="var(--text-muted)" />
                  {s.filename}
                </Link>
              </td>
              <td className="text-sm">{s.student_name || s.student_id}</td>
              <td>{s.page_count ?? '—'}</td>
              <td><StatusBadge status={s.status} /></td>
              <td className="text-xs text-muted">{s.created_at?.slice(0, 10)}</td>
              <td className="text-right">
                <Link to={paths.script(s.script_id)} className="btn btn-ghost btn-sm">
                  Open <ChevronRight size={14} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
