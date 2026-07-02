import { useCallback, useMemo, useState } from 'react'
import { loadOffsets, saveOffsets } from './layout'

// ── department-box drag: move the whole cluster as a unit ──────────────────────
// Encapsulates the owner's manual arrangement: the per-department {dx,dy} offset
// state (seeded from localStorage so it survives reloads/Refresh), the drag handlers
// that live-move a box + its member brains together and commit the absolute offset on
// drop, the persistence, and the Reset control. `refitTick` is bumped by Reset so the
// composition can re-fit the graph after offsets clear (a manual drag deliberately
// does NOT re-fit — it would fight the drag).
export function useDeptDrag({ groupNodes, layout, nodeDept, setRfNodes }) {
  // Per-department drag offsets {dept: {dx,dy}} — the user's manual arrangement,
  // seeded from localStorage so it survives reloads/Refresh. Applied to each dept
  // box AND its member brains so the cluster moves as a unit.
  const [deptOffsets, setDeptOffsets] = useState(loadOffsets)
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
      return { ...rn, position: { x: b.x + off.dx, y: b.y + off.dy } }
    }))
  }, [baseBoxByDept, nodeDept, layout, setRfNodes])

  const onNodeDragStop = useCallback((_e, node) => {
    if (node.type !== 'deptGroup') return
    const dept = node.data.department
    const base = baseBoxByDept[dept]
    if (!base) return
    const off = { dx: node.position.x - base.x, dy: node.position.y - base.y }
    setDeptOffsets((prev) => {
      const next = { ...prev, [dept]: off }
      saveOffsets(next)
      return next
    })
  }, [baseBoxByDept])

  const resetLayout = useCallback(() => {
    setDeptOffsets({})
    saveOffsets({})
    setRefitTick((t) => t + 1)
  }, [])
  const hasOffsets = Object.keys(deptOffsets).length > 0

  return { deptOffsets, refitTick, onNodeDrag, onNodeDragStop, resetLayout, hasOffsets }
}
