import React, { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, RotateCcw } from 'lucide-react'
import { api } from '../api'
import { ScanImage } from './ui'

/**
 * Browses every rendered page of a script's scan, defaulting to the page a
 * particular question's answer was extracted from. Lets a teacher slide
 * through the whole script for context (e.g. an answer that continues onto
 * the next page, or to double-check a transcription) instead of only ever
 * seeing one fixed page image with no way to look around it.
 *
 * scriptId    — which script's rendered pages to pull from
 * answerPage  — the page this question's answer was extracted from (may be
 *               null if extraction never recorded one)
 * pageCount   — total rendered pages for the script; when unknown, falls
 *               back to showing just answerPage with no slider
 */
export default function PageSlider({ scriptId, answerPage, pageCount }) {
  const [page, setPage] = useState(answerPage ?? 1)

  // Jump back to this question's answer page whenever the question (or
  // script) being viewed changes, rather than keeping wherever the previous
  // question's slider was left.
  useEffect(() => setPage(answerPage ?? 1), [scriptId, answerPage])

  if (!pageCount) {
    return answerPage != null ? (
      <ScanImage
        src={api.scripts.pageImageUrl(scriptId, answerPage)}
        alt={`Scanned page ${answerPage}`}
        style={{ maxWidth: '100%', maxHeight: 320, objectFit: 'contain' }}
      />
    ) : (
      <div className="text-xs text-muted" style={{ padding: 8 }}>
        No page recorded for this question, so there's no scan to show.
      </div>
    )
  }

  const clamp = (n) => Math.min(pageCount, Math.max(1, n))
  const onAnswerPage = answerPage != null && page === answerPage

  return (
    <div>
      <div className="crop-frame" style={{ maxHeight: 320 }}>
        <ScanImage
          src={api.scripts.pageImageUrl(scriptId, page)}
          alt={`Scanned page ${page} of ${pageCount}`}
          style={{ maxWidth: '100%', maxHeight: 300, objectFit: 'contain' }}
        />
      </div>
      <div className="flex items-center gap-2 mt-2">
        <button
          className="btn btn-ghost btn-sm btn-icon"
          onClick={() => setPage((p) => clamp(p - 1))}
          disabled={page <= 1}
          aria-label="Previous page"
        >
          <ChevronLeft size={14} />
        </button>
        <input
          type="range"
          min={1}
          max={pageCount}
          value={page}
          onChange={(e) => setPage(clamp(Number(e.target.value)))}
          style={{ flex: 1 }}
          aria-label={`Page ${page} of ${pageCount}`}
        />
        <button
          className="btn btn-ghost btn-sm btn-icon"
          onClick={() => setPage((p) => clamp(p + 1))}
          disabled={page >= pageCount}
          aria-label="Next page"
        >
          <ChevronRight size={14} />
        </button>
        <span className="text-xs text-muted" style={{ width: 60, textAlign: 'right' }}>
          Page {page}/{pageCount}
        </span>
        {answerPage != null && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setPage(answerPage)}
            disabled={onAnswerPage}
            title={`Jump back to page ${answerPage}, where this answer was extracted`}
          >
            <RotateCcw size={12} /> Answer page
          </button>
        )}
      </div>
    </div>
  )
}
