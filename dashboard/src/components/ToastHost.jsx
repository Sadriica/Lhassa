import { useEffect, useState } from 'react'
import { subscribe, dismiss } from '../lib/toast'

/**
 * ToastHost — the single, app-level render surface for the toast store.
 * Mount it ONCE (App.jsx). Panels never render toasts themselves; they call
 * `toast.success(...)` / `toast.error(...)` (see src/lib/toast.js) and this host
 * paints whatever is queued. Top-right on desktop, bottom on mobile.
 */
export default function ToastHost() {
  const [items, setItems] = useState([])

  useEffect(() => subscribe(setItems), [])

  if (items.length === 0) return null

  return (
    <div className="lh-toast-region" role="status" aria-live="polite" aria-atomic="false">
      {items.map((t) => (
        <div key={t.id} className={`lh-toast lh-toast-${t.type}`}>
          <span className="lh-toast-mark" aria-hidden="true" />
          <div className="lh-toast-body">{t.message}</div>
          {t.action && (
            <button
              type="button"
              className="lh-toast-action"
              onClick={() => {
                try {
                  t.action.onClick?.()
                } finally {
                  dismiss(t.id)
                }
              }}
            >
              {t.action.label}
            </button>
          )}
          <button
            type="button"
            className="lh-toast-close"
            aria-label="Dismiss notification"
            onClick={() => dismiss(t.id)}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
