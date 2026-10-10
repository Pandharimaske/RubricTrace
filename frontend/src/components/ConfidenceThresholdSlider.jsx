import React, { useState } from 'react'
import { Info, SlidersHorizontal } from 'lucide-react'

/**
 * Confidence threshold slider for teachers to control which answers
 * get flagged for review. Lower threshold = fewer items flagged,
 * higher threshold = more items flagged.
 */
export default function ConfidenceThresholdSlider({
  value = 0.65,
  onChange,
  disabled = false,
  showDescription = true,
  size = 'normal' // 'normal' or 'compact'
}) {
  const [localValue, setLocalValue] = useState(value)

  // Update local state when prop changes
  React.useEffect(() => {
    setLocalValue(value)
  }, [value])

  const handleChange = (e) => {
    const newValue = parseFloat(e.target.value)
    setLocalValue(newValue)
    onChange?.(newValue)
  }

  const percentage = Math.round(localValue * 100)

  const isCompact = size === 'compact'

  return (
    <div className={`confidence-threshold-slider ${isCompact ? 'compact' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <label className="form-label flex items-center gap-2" style={{ margin: 0 }}>
          <SlidersHorizontal size={isCompact ? 14 : 16} />
          Confidence threshold
        </label>
        <div className="flex items-center gap-2">
          <span className="badge" style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            fontWeight: 600,
            minWidth: '60px',
            textAlign: 'center'
          }}>
            {percentage}%
          </span>
        </div>
      </div>

      {showDescription && !isCompact && (
        <div className="flex items-start gap-2 mb-3" style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
          <Info size={14} style={{ marginTop: 1, flexShrink: 0 }} />
          <p style={{ margin: 0, lineHeight: 1.4 }}>
            Answers with confidence below this level are flagged for your review.
            Lower the threshold to review fewer items, raise it to catch more edge cases.
          </p>
        </div>
      )}

      <div className="slider-container">
        <input
          type="range"
          min="0.05"
          max="1.0"
          step="0.05"
          value={localValue}
          onChange={handleChange}
          disabled={disabled}
          className="confidence-slider"
          aria-label="Confidence threshold percentage"
          aria-valuemin="5"
          aria-valuemax="100"
          aria-valuenow={percentage}
          aria-valuetext={`${percentage}% confidence threshold`}
        />
        <div className="slider-labels">
          <span className="text-xs text-muted">5%</span>
          <span className="text-xs text-muted">50%</span>
          <span className="text-xs text-muted">100%</span>
        </div>
      </div>

      <div className="flex items-center justify-between mt-2">
        <div className="text-xs text-muted">
          <span style={{ color: localValue < 0.5 ? 'var(--success-light)' : localValue < 0.75 ? 'var(--warning-light)' : 'var(--danger)' }}>
            {localValue < 0.5 ? 'Relaxed' : localValue < 0.75 ? 'Balanced' : 'Strict'}
          </span>
          {' '}review policy
        </div>
        {isCompact && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => onChange?.(0.65)}
            disabled={disabled}
            style={{ fontSize: '11px', padding: '2px 8px' }}
          >
            Reset to 65%
          </button>
        )}
      </div>
    </div>
  )
}
