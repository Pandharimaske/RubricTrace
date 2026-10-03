import React from 'react'
import { Link } from 'react-router-dom'
import {
  Activity, Users, FileText, AlertTriangle, CheckCircle, Brain, TrendingUp, ClipboardList,
} from 'lucide-react'
import { useStats, useScripts } from '../hooks/queries'
import { paths } from '../lib/routes'
import { PageHeader, QueryGate, StatusBadge, EmptyState } from '../components/ui'

export default function Dashboard() {
  const stats = useStats()
  const scripts = useScripts()

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Overview of grading activity and teacher review queue."
      />
      <QueryGate query={scripts} loadingLabel="Loading dashboard…">
        {(allScripts) => (
          <DashboardBody stats={stats.data} recent={recentFirst(allScripts).slice(0, 5)} />
        )}
      </QueryGate>
    </div>
  )
}

const recentFirst = (list) =>
  [...list].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))

function DashboardBody({ stats, recent }) {
  const statCards = [
    { label: 'Students',         value: stats?.total_students,         icon: Users,         accent: '#6366f1' },
    { label: 'Scripts Uploaded', value: stats?.total_scripts,          icon: FileText,      accent: '#3b82f6' },
    { label: 'Questions Graded', value: stats?.total_questions_graded, icon: Brain,         accent: '#8b5cf6' },
    { label: 'Needs Review',     value: stats?.needs_review,           icon: AlertTriangle, accent: '#f59e0b' },
    { label: 'Teacher Approved', value: stats?.approved,               icon: CheckCircle,   accent: '#10b981' },
    { label: 'Auto Scored',      value: stats?.scored,                 icon: Activity,      accent: '#06b6d4' },
  ]

  return (
    <>
      <div className="stats-grid">
        {statCards.map(({ label, value, icon: Icon, accent }) => (
          <div key={label} className="stat-card" style={{ '--accent': accent }}>
            <div className="stat-label">{label}</div>
            <div className="stat-value">{value ?? '–'}</div>
            <div className="stat-icon"><Icon size={32} /></div>
          </div>
        ))}
      </div>

      {stats?.needs_review > 0 && (
        <div className="alert alert-warning mb-4">
          <AlertTriangle size={16} />
          <div>
            <strong>{stats.needs_review} questions</strong> are waiting for teacher review.{' '}
            <Link to={paths.review} className="btn btn-sm btn-warning" style={{ marginLeft: 8 }}>
              Go to Review Queue
            </Link>
          </div>
        </div>
      )}

      <div className="grid-2 mb-6">
        <div className="card">
          <div className="card-header"><h3 className="card-title">Quick Actions</h3></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Link to={paths.exams} className="btn btn-primary">
              <ClipboardList size={16} /> Go to Exams
            </Link>
            <Link to={paths.review} className="btn btn-secondary">
              <AlertTriangle size={16} /> Open Review Queue
            </Link>
            <Link to={paths.rubrics} className="btn btn-ghost">
              <TrendingUp size={16} /> Manage Rubric Configs
            </Link>
          </div>
        </div>

        <div className="card">
          <div className="card-header"><h3 className="card-title">Recent Scripts</h3></div>
          {recent.length === 0 ? (
            <EmptyState
              icon="📄"
              title="No scripts yet"
              description="Upload a student PDF to get started."
            />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {recent.map((s) => (
                <Link
                  key={s.script_id}
                  to={paths.script(s.script_id)}
                  className="card interactive"
                  style={{ padding: '10px 14px' }}
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                        {s.filename}
                      </div>
                      <div className="text-xs text-muted">{s.student_name || s.student_id}</div>
                    </div>
                    <StatusBadge status={s.status} />
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
