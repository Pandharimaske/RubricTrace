import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Users, Plus, FileText, ChevronRight, Search } from 'lucide-react'
import { useStudents, useCreateStudent } from '../hooks/queries'
import { useToast } from '../components/Toast'
import { paths } from '../lib/routes'
import { PageHeader, QueryGate, EmptyState } from '../components/ui'

export default function StudentsPage() {
  const students = useStudents()
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)

  return (
    <div>
      <PageHeader
        title="Students"
        description="Manage student records and view their uploaded scripts."
        actions={
          <button className="btn btn-primary" onClick={() => setCreating((v) => !v)}>
            <Plus size={16} /> Add Student
          </button>
        }
      />

      {creating && <NewStudentForm onDone={() => setCreating(false)} />}

      <div className="card mb-4 search-bar">
        <Search size={16} color="var(--text-muted)" />
        <input
          className="form-input"
          placeholder="Search by name or ID…"
          aria-label="Search students"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <QueryGate query={students} loadingLabel="Loading students…">
        {(data) => <StudentsTable students={data} search={search} />}
      </QueryGate>
    </div>
  )
}

function NewStudentForm({ onDone }) {
  const create = useCreateStudent()
  const toast = useToast()
  const [name, setName] = useState('')
  const [studentId, setStudentId] = useState('')

  function submit(e) {
    e.preventDefault()
    if (!name.trim()) return
    create.mutate(
      { name: name.trim(), student_id: studentId.trim() || undefined },
      {
        onSuccess: () => {
          toast.success(`Added ${name.trim()}`)
          onDone()
        },
        onError: (err) => toast.error(`Could not add student: ${err.message}`),
      },
    )
  }

  return (
    <form className="card mb-4" onSubmit={submit}>
      <h3 className="card-title mb-3">New Student</h3>
      <div className="grid-2">
        <div className="form-group">
          <label className="form-label" htmlFor="new-student-name">Name *</label>
          <input
            id="new-student-name"
            className="form-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Arjun Mehta"
            autoFocus
            required
          />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="new-student-id">Student ID (optional)</label>
          <input
            id="new-student-id"
            className="form-input"
            value={studentId}
            onChange={(e) => setStudentId(e.target.value)}
            placeholder="Auto-generated if blank"
          />
        </div>
      </div>
      <div className="flex gap-2">
        <button type="submit" className="btn btn-primary btn-sm" disabled={create.isPending || !name.trim()}>
          {create.isPending ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onDone}>Cancel</button>
      </div>
    </form>
  )
}

function StudentsTable({ students, search }) {
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase()
    return students.filter(
      (s) => !q || s.name?.toLowerCase().includes(q) || s.student_id?.toLowerCase().includes(q),
    )
  }, [students, search])

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<Users size={48} />}
        title={search ? 'No matching students' : 'No students yet'}
        description="Upload a script and the student will be created automatically."
      />
    )
  }

  return (
    <div className="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Student</th>
            <th>ID</th>
            <th>Scripts</th>
            <th>Registered</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.student_id}>
              <td style={{ fontWeight: 600 }}>
                <Link to={paths.student(s.student_id)} style={{ color: 'var(--text-primary)', textDecoration: 'none' }}>
                  {s.name || '—'}
                </Link>
              </td>
              <td className="font-mono text-xs">{s.student_id}</td>
              <td>
                <span className="badge badge-info"><FileText size={10} /> {s.script_count}</span>
              </td>
              <td className="text-xs text-muted">{s.created_at?.slice(0, 10)}</td>
              <td className="text-right">
                <Link to={paths.student(s.student_id)} className="btn btn-ghost btn-sm">
                  View <ChevronRight size={14} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
