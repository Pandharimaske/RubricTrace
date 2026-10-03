import React from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

/** Catches render errors so one broken page doesn't blank the whole app. */
export default class ErrorBoundary extends React.Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('UI error:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="empty-state">
        <AlertTriangle size={36} color="var(--danger)" />
        <div className="empty-state-title">Something went wrong on this page</div>
        <div className="empty-state-desc">{this.state.error.message}</div>
        <button className="btn btn-secondary" onClick={() => this.setState({ error: null })}>
          <RefreshCw size={14} /> Try again
        </button>
      </div>
    )
  }
}
