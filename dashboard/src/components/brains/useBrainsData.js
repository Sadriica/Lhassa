import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from '../../lib/toast'
import { reparentTopology, removeNodeLocal, setNodeSkills } from './topology'

// The Brains data layer — drives the slice-J org API:
//   GET    /api/org/topology              read the chart
//   GET    /api/org/skills                populate the skills picker
//   GET    /api/org/route?goal=…          preview a goal's root→leaf path
//   POST   /api/org/brains                create
//   PATCH  /api/org/brains/{id}           edit skills/limits/role/department
//   POST   /api/org/brains/{id}/move      re-parent (who reports to whom)
//   POST   /api/org/brains/{id}/promote   promote to root (commands everyone)
//   DELETE /api/org/brains/{id}?force=    delete (force removes the subtree)
// Every mutating call returns the updated chart, so we adopt the response
// directly (optimistic refresh) and fall back to a refetch on error.
// The hook holds all topology/overlay/selection state + every fetch/mutate/
// optimistic-undo function, returning them to the composition (BrainsPanel).
export function useBrainsData() {
  const [topology, setTopology] = useState(null)
  const [skills, setSkills] = useState([])
  const [installed, setInstalled] = useState([]) // installed SKILL.md skills
  const [skillNotice, setSkillNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [goal, setGoal] = useState('')
  const [route, setRoute] = useState(null)
  const [routeErr, setRouteErr] = useState('')
  const [view, setView] = useState('flow') // 'flow' (n8n-style canvas) | 'tree' | 'templates'
  // The typed relationship OVERLAY (separate graph from the command tree): a flat
  // edge list for the canvas + inspector, plus the kind vocabulary for the legend.
  const [overlay, setOverlay] = useState({ relationships: [], kinds: [] })
  const [relKinds, setRelKinds] = useState([]) // [{kind, description, symmetric}]

  const loadTopology = useCallback(async () => {
    try {
      const r = await fetch('/api/org/topology')
      if (!r.ok) throw new Error('topology fetch failed')
      const d = await r.json()
      setTopology(d)
      setError('')
    } catch {
      setError('Could not load the org chart.')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadInstalled = useCallback(async () => {
    try {
      const r = await fetch('/api/skills')
      const d = r.ok ? await r.json() : []
      setInstalled(Array.isArray(d) ? d : [])
    } catch {
      setInstalled([])
    }
  }, [])

  // The relationship overlay (typed edges) — same fetch/adopt pattern as topology.
  const loadOverlay = useCallback(async () => {
    try {
      const r = await fetch('/api/org/relationships')
      const d = r.ok ? await r.json() : null
      setOverlay(d && Array.isArray(d.relationships) ? d : { relationships: [], kinds: [] })
    } catch {
      setOverlay({ relationships: [], kinds: [] })
    }
  }, [])

  useEffect(() => {
    loadTopology()
    loadInstalled()
    loadOverlay()
    fetch('/api/org/skills')
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => setSkills(Array.isArray(d) ? d : []))
      .catch(() => setSkills([]))
    // The kind vocabulary (legend descriptions + which kinds are symmetric).
    fetch('/api/org/relationships/kinds')
      .then((r) => (r.ok ? r.json() : { kinds: [] }))
      .then((d) => setRelKinds(Array.isArray(d.kinds) ? d.kinds : []))
      .catch(() => setRelKinds([]))
  }, [loadTopology, loadInstalled, loadOverlay])

  // #6 — the canvas can go stale when the topology/overlay changes elsewhere (another
  // tab, the CLI, a heartbeat). Real-time WS push is out of scope here; a lightweight
  // focus-refetch is enough: re-pull the chart + overlay when the tab/window regains
  // focus, debounced so a focus+visibilitychange burst fires only one fetch. The
  // listeners are cleaned up on unmount.
  useEffect(() => {
    let t = null
    const refetch = () => {
      if (document.visibilityState !== 'visible') return
      clearTimeout(t)
      t = setTimeout(() => { loadTopology(); loadOverlay() }, 300)
    }
    const onVisible = () => { if (document.visibilityState === 'visible') refetch() }
    window.addEventListener('focus', refetch)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearTimeout(t)
      window.removeEventListener('focus', refetch)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [loadTopology, loadOverlay])

  // The set a carried skill is matched against — installed skill names AND their
  // domains (the org picker keys by domain, SKILL.md by name; accept either).
  const installedSet = useMemo(() => {
    const s = new Set()
    for (const sk of installed) {
      if (sk?.name) s.add(String(sk.name).toLowerCase())
      const dom = sk?.metadata?.domain
      if (dom) s.add(String(dom).toLowerCase())
    }
    return s
  }, [installed])

  const installSkill = useCallback(async (name) => {
    setSkillNotice('')
    try {
      const r = await fetch(`/api/skills/${encodeURIComponent(name)}/install`, { method: 'POST' })
      if (r.status === 404) {
        setSkillNotice(`"${name}" isn't in the catalog — open Skills to create it with AI.`)
        return
      }
      if (!r.ok) throw new Error('install failed')
      await loadInstalled()
      setSkillNotice(`Installed "${name}".`)
    } catch {
      setSkillNotice(`Could not install "${name}".`)
    }
  }, [loadInstalled])

  // A mutating call returns the updated chart — adopt it (optimistic refresh).
  const mutate = useCallback(async (label, doFetch) => {
    setBusy(true)
    setError('')
    try {
      const r = await doFetch()
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `${label} failed`)
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      else await loadTopology()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : `${label} failed`)
      return false
    } finally {
      setBusy(false)
    }
  }, [loadTopology])

  const createBrain = async (payload, { keepOpen = false } = {}) => {
    const ok = await mutate('Create', () =>
      fetch('/api/org/brains', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }))
    if (ok) {
      toast.success(`Created "${payload.id}"`)
      // "Create & add another" keeps the form open (and unselected) so the owner can
      // keep adding siblings; the normal path selects the new brain and closes.
      if (!keepOpen) { setShowCreate(false); setSelectedId(payload.id) }
    } else {
      toast.error(`Could not create "${payload.id}"`)
    }
    return ok
  }
  const patchBrain = (id, payload) =>
    mutate('Save', () =>
      fetch(`/api/org/brains/${encodeURIComponent(id)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }))
  // #10 — Undo a move: put the brain back under its PREVIOUS parent (promote if it was
  // a root). Non-optimistic (adopts the canonical chart), so the restore is exact.
  const undoMove = useCallback(async (id, prevParent) => {
    if (prevParent) {
      const ok = await mutate('Move', () =>
        fetch(`/api/org/brains/${encodeURIComponent(id)}/move`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parent: prevParent }),
        }))
      if (ok) toast.success(`Moved "${id}" back under "${prevParent}"`)
      else toast.error(`Could not move "${id}" back`)
    } else {
      const ok = await mutate('Promote', () =>
        fetch(`/api/org/brains/${encodeURIComponent(id)}/promote`, { method: 'POST' }))
      if (ok) toast.success(`Moved "${id}" back to root`)
      else toast.error(`Could not move "${id}" back`)
    }
  }, [mutate])

  // #10 — recreate a just-deleted LEAF from its cached data (Undo). Reuses the create
  // path; on success the restored brain is re-selected so it's visible again.
  const recreateBrain = useCallback(async (cached) => {
    if (!cached) return
    const ok = await mutate('Create', () =>
      fetch('/api/org/brains', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: cached.id,
          role: cached.role || undefined,
          parent: cached.parent || undefined,
          department: cached.department || undefined,
          skills: cached.skills || [],
          limits: cached.limits || {},
        }),
      }))
    if (ok) { setSelectedId(cached.id); toast.success(`Restored "${cached.id}"`) }
    else toast.error(`Could not restore "${cached.id}"`)
  }, [mutate])

  // #10 — Undo a skill removal: restore the EXACT prior skill set via PATCH.
  const reAddSkill = useCallback(async (brainId, skill, prevSkills) => {
    const ok = await mutate('Assign', () =>
      fetch(`/api/org/brains/${encodeURIComponent(brainId)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skills: prevSkills }),
      }))
    if (ok) toast.success(`Re-added "${skill}"`)
    else toast.error(`Could not re-add "${skill}"`)
  }, [mutate])

  // #10 — remove a skill from a brain (the ✕): optimistic removal from the canvas, then
  // persist via PATCH (the update path replaces the skill set). The success toast offers
  // Undo → restore the prior set. On failure the optimistic topology change is reverted.
  const removeSkillFromBrain = useCallback(async (brainId, skill) => {
    const prevTopology = topology
    const node = (topology?.nodes || []).find((n) => n.id === brainId)
    if (!node) return
    const prevSkills = node.skills || []
    const nextSkills = prevSkills.filter((s) => s !== skill)
    setTopology((t) => setNodeSkills(t, brainId, nextSkills))
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/org/brains/${encodeURIComponent(brainId)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skills: nextSkills }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Remove failed')
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      toast.show({
        type: 'success',
        message: `Removed "${skill}" from "${brainId}"`,
        action: { label: 'Undo', onClick: () => reAddSkill(brainId, skill, prevSkills) },
      })
    } catch (e) {
      setTopology(prevTopology)
      setError(e instanceof Error ? e.message : 'Remove failed')
      toast.error(`Could not remove "${skill}"`)
    } finally {
      setBusy(false)
    }
  }, [topology, reAddSkill])

  // #10 — MOVE is optimistic + undoable. Re-parent locally the instant the drag/menu/
  // dropdown fires, then commit to the backend: adopt the canonical chart on success,
  // roll back to the exact pre-move topology on failure. The success toast carries an
  // Undo that moves the brain back under its PREVIOUS parent.
  const moveBrain = useCallback(async (id, parent) => {
    const prevTopology = topology
    const node = (topology?.nodes || []).find((n) => n.id === id)
    if (!node) return false
    const prevParent = node.parent || ''
    setTopology((t) => reparentTopology(t, id, parent))
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/org/brains/${encodeURIComponent(id)}/move`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ parent }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Move failed')
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      toast.show({
        type: 'success',
        message: `Moved "${id}" under "${parent}"`,
        action: { label: 'Undo', onClick: () => undoMove(id, prevParent) },
      })
      return true
    } catch (e) {
      setTopology(prevTopology)
      setError(e instanceof Error ? e.message : 'Move failed')
      toast.error(`Could not move "${id}"`)
      return false
    } finally {
      setBusy(false)
    }
  }, [topology, undoMove])
  const promoteBrain = (id) =>
    mutate('Promote', () =>
      fetch(`/api/org/brains/${encodeURIComponent(id)}/promote`, { method: 'POST' }))
  // Add a recommended skill to a brain: install it from the catalog if needed
  // (ignore 404 — a recommendation may not be a catalog skill), then assign it.
  // The assign endpoint returns {ok}, not a chart, so we refetch the topology (and
  // the installed set) to refresh the carried-skills section.
  const addSkillToBrain = useCallback(async (brainId, name) => {
    setBusy(true)
    setError('')
    try {
      await fetch(`/api/skills/${encodeURIComponent(name)}/install`, { method: 'POST' }).catch(() => {})
      const r = await fetch(`/api/skills/${encodeURIComponent(name)}/assign`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ brain_id: brainId }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Assign failed')
      }
      await loadTopology()
      await loadInstalled()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Assign failed')
      return false
    } finally {
      setBusy(false)
    }
  }, [loadTopology, loadInstalled])

  // #10 — DELETE. A LEAF (no children) is optimistic + undoable: it disappears from the
  // canvas immediately and the success toast offers Undo, which RECREATES it from the
  // cached (id, role, parent, department, skills, limits) via the create path. A brain
  // WITH children is a force/subtree delete — effectively irreversible, so it keeps the
  // (caller's) two-step confirm and gets a plain, non-undoable success toast that says so.
  const deleteBrain = useCallback(async (id, force) => {
    const node = (topology?.nodes || []).find((n) => n.id === id)
    if (force) {
      const ok = await mutate('Delete', () =>
        fetch(`/api/org/brains/${encodeURIComponent(id)}?force=true`, { method: 'DELETE' }))
      if (ok) {
        if (selectedId === id) setSelectedId(null)
        loadOverlay() // a removed subtree may have left dangling overlay edges
        toast.success(`Deleted "${id}" and its sub-brains — the whole subtree was removed.`)
      } else {
        toast.error(`Could not delete "${id}"`)
      }
      return
    }
    // Leaf — optimistic + undoable.
    const prevTopology = topology
    const cached = node
      ? {
          id: node.id,
          role: node.role,
          parent: node.parent,
          department: node.department,
          skills: node.skills || [],
          limits: node.limits || {},
        }
      : null
    setTopology((t) => removeNodeLocal(t, id))
    if (selectedId === id) setSelectedId(null)
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`/api/org/brains/${encodeURIComponent(id)}?force=false`, { method: 'DELETE' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Delete failed')
      }
      const chart = await r.json()
      if (chart && chart.nodes) setTopology(chart)
      loadOverlay() // a removed brain may have left dangling overlay edges
      toast.show({
        type: 'success',
        message: `Deleted "${id}"`,
        action: cached ? { label: 'Undo', onClick: () => recreateBrain(cached) } : null,
      })
    } catch (e) {
      setTopology(prevTopology)
      setError(e instanceof Error ? e.message : 'Delete failed')
      toast.error(`Could not delete "${id}"`)
    } finally {
      setBusy(false)
    }
  }, [topology, selectedId, loadOverlay, mutate, recreateBrain])

  // Relationship overlay mutations. POST/DELETE return the updated overlay (describe),
  // so we adopt the response directly — the canvas (OrgFlow) and the inspector both
  // read `overlay`, so they refresh together. Returns {ok, detail} so the inspector
  // can surface the backend's 400/404 message inline.
  const addRelationship = useCallback(async (from, to, kind) => {
    try {
      const r = await fetch('/api/org/relationships', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from, to, kind }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) return { ok: false, detail: d.detail || 'Add failed' }
      if (d && Array.isArray(d.relationships)) setOverlay(d)
      else await loadOverlay()
      return { ok: true }
    } catch {
      return { ok: false, detail: 'Add failed' }
    }
  }, [loadOverlay])

  const removeRelationship = useCallback(async (from, to, kind) => {
    try {
      const r = await fetch('/api/org/relationships', {
        method: 'DELETE', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from, to, kind }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) return { ok: false, detail: d.detail || 'Remove failed' }
      if (d && Array.isArray(d.relationships)) setOverlay(d)
      else await loadOverlay()
      return { ok: true }
    } catch {
      return { ok: false, detail: 'Remove failed' }
    }
  }, [loadOverlay])

  const previewRoute = async () => {
    const g = goal.trim()
    if (!g) return
    setRouteErr('')
    setRoute(null)
    try {
      const r = await fetch(`/api/org/route?goal=${encodeURIComponent(g)}`)
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || 'Route preview failed')
      }
      setRoute(await r.json())
    } catch (e) {
      setRouteErr(e instanceof Error ? e.message : 'Route preview failed')
    }
  }

  const nodes = topology?.nodes || []
  const allIds = useMemo(() => (topology?.nodes || []).map((n) => n.id), [topology])
  // Existing departments = distinct non-empty `department` values across the chart.
  const departments = useMemo(() => {
    const set = new Set()
    for (const n of topology?.nodes || []) {
      const d = (n.department || '').trim()
      if (d) set.add(d)
    }
    return [...set].sort((a, b) => a.localeCompare(b))
  }, [topology])
  const selected = nodes.find((n) => n.id === selectedId) || null
  const routePath = route?.path || []
  const isStandalone = topology?.standalone

  return {
    // state + setters the composition needs
    topology, setTopology,
    skills,
    skillNotice,
    loading, error, busy,
    selectedId, setSelectedId,
    showCreate, setShowCreate,
    goal, setGoal, route, routeErr,
    view, setView,
    overlay, relKinds,
    installedSet,
    // loaders + mutations
    loadTopology,
    installSkill,
    createBrain, patchBrain,
    removeSkillFromBrain,
    moveBrain, promoteBrain,
    addSkillToBrain,
    deleteBrain,
    addRelationship, removeRelationship,
    previewRoute,
    // derived
    nodes, allIds, departments, selected, routePath, isStandalone,
  }
}
