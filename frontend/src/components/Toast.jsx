import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle, Info, X } from 'lucide-react'

const ToastContext = createContext(null)

const ICONS = { success: CheckCircle, error: AlertTriangle, info: Info }

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const nextId = useRef(0)

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  const push = useCallback(
    (type, message) => {
      const id = nextId.current++
      setToasts((list) => [...list, { id, type, message }])
      // Errors stay longer so they can actually be read.
      setTimeout(() => dismiss(id), type === 'error' ? 8000 : 4000)
    },
    [dismiss],
  )

  const toast = useMemo(
    () => ({
      success: (m) => push('success', m),
      error: (m) => push('error', m),
      info: (m) => push('info', m),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {toasts.map(({ id, type, message }) => {
          const Icon = ICONS[type]
          return (
            <div key={id} className={`toast toast-${type}`}>
              <Icon size={16} />
              <span>{message}</span>
              <button
                className="toast-close"
                onClick={() => dismiss(id)}
                aria-label="Dismiss notification"
              >
                <X size={14} />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}
