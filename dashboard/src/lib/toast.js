/**
 * toast; the dashboard's single, dependency-free action-feedback channel.
 *
 * ── CONTRACT FOR PANEL AUTHORS ────────────────────────────────────────────────
 * Any panel can surface a global toast without importing UI or wiring props:
 *
 *     import { toast } from '../lib/toast'
 *
 *     toast.success('Skill installed')
 *     toast.error('Could not save flow')
 *     toast.info('Reconnecting…')
 *
 * Full control (custom duration, or an optional action button; e.g. undo):
 *
 *     toast.show({
 *       type: 'success',                 // 'success' | 'error' | 'info'
 *       message: 'Flow deleted',
 *       duration: 6000,                  // ms; omit for the type default
 *       action: { label: 'Undo', onClick: () => restore() },  // optional
 *     })
 *
 * Every helper returns the toast id; pass it to `toast.dismiss(id)` to close it
 * early. `<ToastHost/>` (mounted once in App.jsx) renders whatever is queued —
 * this module is the only dependency panels rely on. Do not render toasts yourself.
 * ──────────────────────────────────────────────────────────────────────────────
 */

// Default lifetimes: errors linger a little longer so they aren't missed.
const DEFAULT_DURATION = 4000
const ERROR_DURATION = 6000

let nextId = 1
const subscribers = new Set()
const timers = new Map() // id → timeout handle

// Live list of active toasts (newest last). The host receives a copy on change.
let toasts = []

function emit() {
  const snapshot = toasts.slice()
  subscribers.forEach((fn) => {
    try {
      fn(snapshot)
    } catch {
      // A broken subscriber must never break the toast pipeline.
    }
  })
}

/**
 * subscribe(fn); register a listener; it's called immediately with the current
 * list and again on every change. Returns an unsubscribe function.
 */
export function subscribe(fn) {
  subscribers.add(fn)
  try {
    fn(toasts.slice())
  } catch {
    // ignore a listener that throws on its first call
  }
  return () => subscribers.delete(fn)
}

/** dismiss(id); remove a toast (and cancel its auto-dismiss timer). */
export function dismiss(id) {
  const timer = timers.get(id)
  if (timer) {
    clearTimeout(timer)
    timers.delete(id)
  }
  const before = toasts.length
  toasts = toasts.filter((t) => t.id !== id)
  if (toasts.length !== before) emit()
}

function show({ type = 'info', message, duration, action } = {}) {
  if (!message) return null
  const id = nextId++
  const ttl =
    typeof duration === 'number'
      ? duration
      : type === 'error'
        ? ERROR_DURATION
        : DEFAULT_DURATION

  const entry = { id, type, message: String(message), action: action || null }
  toasts = [...toasts, entry]
  emit()

  if (ttl > 0 && Number.isFinite(ttl)) {
    timers.set(
      id,
      setTimeout(() => dismiss(id), ttl),
    )
  }
  return id
}

export const toast = {
  show,
  success: (message, opts) => show({ ...opts, type: 'success', message }),
  error: (message, opts) => show({ ...opts, type: 'error', message }),
  info: (message, opts) => show({ ...opts, type: 'info', message }),
  dismiss,
  subscribe,
}

export default toast
