import { useCallback, useMemo, useState } from 'react'
import { loadOrgLayout, saveOrgLayout } from './layout'

// ── department-box drag: move the whole cluster as a unit ──────────────────────
// Encapsulates the owner's manual arrangement: the per-department {dx,dy} offset
// state (seeded from localStorage so it survives reloads/Refresh), the drag handlers
// that live-move a box + its member brains together and commit the absolute offset on
// drop, the persistence, and the Reset control. `refitTick` is bumped by Reset so the
// composition can re-fit the graph after offsets clear (a manual drag deliberately
// does NOT re-fit — it would fight the drag).
export function useDeptDrag({ groupNodes, layout, nodeDept, setRfNodes }) {
  const [orgLayout, setOrgLayout] = useState(loadOrgLayout)
  const deptLayouts = useMemo(() => orgLayout.departments || {}, [orgLayout.departments])
  const nodeOffsets = useMemo(() => orgLayout.nodes || {}, [orgLayout.nodes])
  const initialViewport = orgLayout.viewport || undefined
  // Base (un-offset) box position per department, so a drag's absolute offset is
  // draggedPosition − basePosition regardless of any previously-applied offset.
  const baseBoxByDept = useMemo(() => {
    const m = {}
    groupNodes.forEach((g) => { m[g.data.department] = g.position })
    return m
  }, [groupNodes])
  // Bumped by Reset to re-fit the graph (fitView otherwise ignores offset changes).
  const [refitTick, setRefitTick] = useState(0)

  // While a department box is dragged, React Flow owns the BOX node's position; we
  // live-move only its member brains so the cluster tracks the box. On drop we commit
  // the department's absolute offset (dragged − base) to state + localStorage; the
  // node-build effect then re-lays box + members consistently (no snap). Brain-node
  // drags fall through untouched (cosmetic; re-parent still happens via the handle).
  const onNodeDrag = useCallback((_e, node) => {
    if (node.type !== 'deptGroup') return
    const dept = node.data.department
    const base = baseBoxByDept[dept]
    if (!base) return
    const off = { dx: node.position.x - base.x, dy: node.position.y - base.y }
    setRfNodes((cur) => cur.map((rn) => {
      if (nodeDept[rn.id] !== dept) return rn
      const b = layout[rn.id]
      if (!b) return rn
      const nodeOff = nodeOffsets[rn.id] || { dx: 0, dy: 0 }
      return { ...rn, position: { x: b.x + off.dx + nodeOff.dx, y: b.y + off.dy + nodeOff.dy } }
    }))
  }, [baseBoxByDept, nodeDept, layout, nodeOffsets, setRfNodes])

  const onNodeDragStop = useCallback((_e, node) => {
    if (node.type === 'deptGroup') {
      const dept = node.data.department
      const base = baseBoxByDept[dept]
      if (!base) return
      const off = { dx: node.position.x - base.x, dy: node.position.y - base.y }
      setOrgLayout((prev) => {
        const next = {
          ...prev,
          departments: {
            ...(prev.departments || {}),
            [dept]: { ...(prev.departments?.[dept] || {}), ...off },
          },
        }
        saveOrgLayout(next)
        return next
      })
      return
    }

    if (node.type === 'brain') {
      const base = layout[node.id]
      if (!base) return
      const dept = nodeDept[node.id]
      const deptOff = dept ? (deptLayouts[dept] || {}) : {}
      const dx = node.position.x - base.x - (deptOff.dx || 0)
      const dy = node.position.y - base.y - (deptOff.dy || 0)
      setOrgLayout((prev) => {
        const next = {
          ...prev,
          nodes: {
            ...(prev.nodes || {}),
            [node.id]: { dx, dy },
          },
        }
        saveOrgLayout(next)
        return next
      })
    }
  }, [baseBoxByDept, deptLayouts, layout, nodeDept])

  const onDeptResizeEnd = useCallback((dept, size) => {
    const width = Number(size?.width)
    const height = Number(size?.height)
    if (!dept || !Number.isFinite(width) || !Number.isFinite(height)) return
    setOrgLayout((prev) => {
      const next = {
        ...prev,
        departments: {
          ...(prev.departments || {}),
          [dept]: { ...(prev.departments?.[dept] || {}), width, height },
        },
      }
      saveOrgLayout(next)
      return next
    })
  }, [])

  const onViewportSave = useCallback((viewport) => {
    if (!viewport || !Number.isFinite(viewport.x) || !Number.isFinite(viewport.y) || !Number.isFinite(viewport.zoom)) return
    setOrgLayout((prev) => {
      const next = { ...prev, viewport: { x: viewport.x, y: viewport.y, zoom: viewport.zoom } }
      saveOrgLayout(next)
      return next
    })
  }, [])

  const resetLayout = useCallback(() => {
    const next = { departments: {}, nodes: {}, viewport: null }
    setOrgLayout(next)
    saveOrgLayout(next)
    setRefitTick((t) => t + 1)
  }, [])
  const hasOffsets = Object.keys(deptLayouts).length > 0 || Object.keys(nodeOffsets).length > 0 || Boolean(orgLayout.viewport)

  return {
    deptLayouts,
    nodeOffsets,
    initialViewport,
    refitTick,
    onNodeDrag,
    onNodeDragStop,
    onDeptResizeEnd,
    onViewportSave,
    resetLayout,
    hasOffsets,
  }
}
