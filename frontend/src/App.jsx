import React from 'react'
import {
  Routes, Route, NavLink, Link, Navigate, useLocation, useOutletContext, useParams,
} from 'react-router-dom'
import {
  LayoutDashboard, ClipboardList, Users, FileText, CheckSquare, BookOpen,
} from 'lucide-react'
import { useHealth, useScript, useStats } from './hooks/queries'
import { useLegacyNavigate } from './hooks/useLegacyNavigate'
import { nextStep } from './lib/exam'
import { paths } from './lib/routes'
import { guardNavigation } from './lib/unsaved'
import ErrorBoundary from './components/ErrorBoundary'
import { EmptyState, QueryGate } from './components/ui'
import Dashboard from './pages/Dashboard'
import UploadPage from './pages/Upload'
import StudentsPage from './pages/Students'
import StudentDetail from './pages/StudentDetail'
import ScriptsPage from './pages/Scripts'
import ScriptDetail from './pages/ScriptDetail'
import ScriptResult from './pages/ScriptResult'
import ReviewQueue from './pages/ReviewQueue'
import RubricConfigs from './pages/RubricConfigs'
import ExamsPage from './pages/Exams'
import ExamWorkspace from './pages/ExamWorkspace'
import ExamRubric from './pages/exam/ExamRubric'
import ExamScripts from './pages/exam/ExamScripts'
import ExamGrade from './pages/exam/ExamGrade'
import ExamReview from './pages/exam/ExamReview'
import ExamResults from './pages/exam/ExamResults'

// Scripts in an exam get the new paper view; older standalone scripts keep the
// original page (which still has its own per-script rubric builder).
function ScriptRoute() {
  const { scriptId } = useParams()
  const navigate = useLegacyNavigate()
  const query = useScript(scriptId)
  return (
    <QueryGate query={query} loadingLabel="Loading script…">
      {(data) =>
        data.script.exam_id ? (
          <ScriptResult data={data} />
        ) : (
          <ScriptDetail key={scriptId} navigate={navigate} scriptId={scriptId} />
        )
      }
    </QueryGate>
  )
}

function RubricConfigsRoute() {
  const navigate = useLegacyNavigate()
  return <RubricConfigs navigate={navigate} />
}

// /exams/:id lands on whichever step needs doing next.
function ExamIndex() {
  const { exam } = useOutletContext()
  return <Navigate to={paths.exam(exam.exam_id, nextStep(exam))} replace />
}

function NotFound() {
  return (
    <EmptyState
      icon="🧭"
      title="Page not found"
      description="That address doesn't exist."
      action={<Link to={paths.dashboard} className="btn btn-secondary">Back to dashboard</Link>}
    />
  )
}

// Moving between an exam's tabs keeps the workspace mounted (no refetch flash);
// any other navigation remounts the page and resets its local state.
const pageKey = (pathname) =>
  pathname.startsWith('/exams/') ? pathname.split('/').slice(0, 3).join('/') : pathname

export default function App() {
  const { pathname } = useLocation()
  const key = pageKey(pathname)

  return (
    <div className="app">
      <Sidebar />
      <main className="main-content">
        <div className="page page-enter" key={key}>
          <ErrorBoundary key={key}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/exams" element={<ExamsPage />} />
              <Route path="/exams/:examId" element={<ExamWorkspace />}>
                <Route index element={<ExamIndex />} />
                <Route path="rubric" element={<ExamRubric />} />
                <Route path="scripts" element={<ExamScripts />} />
                <Route path="grade" element={<ExamGrade />} />
                <Route path="review" element={<ExamReview />} />
                <Route path="results" element={<ExamResults />} />
              </Route>
              <Route path="/upload" element={<UploadPage />} />
              <Route path="/students" element={<StudentsPage />} />
              <Route path="/students/:studentId" element={<StudentDetail />} />
              <Route path="/scripts" element={<ScriptsPage />} />
              <Route path="/scripts/:scriptId" element={<ScriptRoute />} />
              <Route path="/review" element={<ReviewQueue />} />
              <Route path="/rubrics" element={<RubricConfigsRoute />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}

const NAV_SECTIONS = [
  {
    label: 'Grading',
    items: [
      { to: paths.dashboard, label: 'Dashboard',    icon: LayoutDashboard, end: true },
      { to: paths.exams,     label: 'Exams',        icon: ClipboardList },
      { to: paths.review,    label: 'Review Queue', icon: CheckSquare, badgeKey: 'needs_review' },
    ],
  },
  {
    label: 'Library',
    items: [
      { to: paths.scripts,  label: 'All Scripts',     icon: FileText },
      { to: paths.students, label: 'Students',        icon: Users },
      { to: paths.rubrics,  label: 'Rubric Templates', icon: BookOpen },
    ],
  },
]

function Sidebar() {
  const health = useHealth()
  const stats = useStats()

  const apiState = health.isPending ? 'connecting' : health.isError ? 'offline' : 'online'
  const apiLabel = { connecting: 'Connecting…', offline: 'API Offline', online: 'API Online' }[apiState]

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <Link to={paths.dashboard} className="logo" onClick={guardNavigation}>
          <div className="logo-mark">📐</div>
          <div>
            <div className="logo-text">RubricTrace</div>
            <span className="logo-sub">Teacher Grading AI</span>
          </div>
        </Link>
      </div>

      <nav className="sidebar-nav" aria-label="Main">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label}>
            <div className="nav-section-label">{section.label}</div>
            {section.items.map(({ to, label, icon: Icon, end, badgeKey }) => {
              const badge = badgeKey ? stats.data?.[badgeKey] : null
              return (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  onClick={guardNavigation}
                  className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
                >
                  <Icon size={16} />
                  <span>{label}</span>
                  {badge > 0 && <span className="nav-badge warning">{badge}</span>}
                </NavLink>
              )
            })}
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="api-status" role="status">
          <div className={`status-dot ${apiState === 'offline' ? 'offline' : ''}`} />
          <span>{apiLabel}</span>
        </div>
      </div>
    </div>
  )
}
