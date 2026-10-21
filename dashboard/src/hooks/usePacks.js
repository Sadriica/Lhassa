import { useEffect, useState } from 'react'
import { getPacks } from '../lib/api'

/**
 * usePacks; the names of the capability packs the backend has loaded, from
 * `GET /api/packs`, as a Set. `null` while loading and whenever the request
 * fails (an older backend without the route, a network error): callers treat
 * null as "unknown" and show every section.
 */
export default function usePacks() {
  const [packs, setPacks] = useState(null)
  useEffect(() => {
    let cancelled = false
    getPacks()
      .then((data) => {
        const names = Array.isArray(data?.packs) ? data.packs.map((p) => p?.name).filter(Boolean) : null
        if (!cancelled && names) setPacks(new Set(names))
      })
      .catch(() => { /* unknown: keep null, show everything */ })
    return () => { cancelled = true }
  }, [])
  return packs
}
