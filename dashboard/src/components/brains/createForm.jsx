import { useState } from 'react'
import { DepartmentField } from './departmentField'
import { SkillPicker } from './skillPicker'
import { LimitsEditor } from './limitsEditor'
import { rowsToLimits } from './limits'

// ── create-brain form ───────────────────────────────────────────────────────
export function CreateForm({ allIds, departments, defaultParent, skills, onCreate, onClose, busy }) {
  const [id, setId] = useState('')
  const [role, setRole] = useState('department')
  const [parent, setParent] = useState(defaultParent || '')
  const [department, setDepartment] = useState('')
  const [pickedSkills, setPickedSkills] = useState([])
  const [limitRows, setLimitRows] = useState([])

  const toggleSkill = (d) =>
    setPickedSkills((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d]))

  // `keepOpen` powers "Create & add another": build a team of siblings fast by
  // keeping the form open and PREFILLED with the same parent/department/role/skills,
  // clearing only the ID so the next sibling is one field away.
  const submit = async (keepOpen = false) => {
    const ok = await onCreate({
      id: id.trim(),
      role: role.trim() || undefined,
      parent: parent || undefined,
      department: department.trim() || undefined,
      skills: pickedSkills,
      limits: rowsToLimits(limitRows),
    }, { keepOpen })
    if (ok && keepOpen) setId('')
  }

  return (
    <div className="lh-card" style={{ background: 'var(--lh-surface-2)' }}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="lh-card-title">New brain</h3>
        <button type="button" className="lh-btn" onClick={onClose}>✕</button>
      </div>
      <div className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="lh-label">id *</span>
            <input className="lh-input" value={id} onChange={(e) => setId(e.target.value)} placeholder="e.g. research" />
          </label>
          <label className="space-y-1">
            <span className="lh-label">role</span>
            <input className="lh-input" value={role} onChange={(e) => setRole(e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="lh-label">parent</span>
            <select className="lh-select" value={parent} onChange={(e) => setParent(e.target.value)}>
              <option value="">— (root, if none yet)</option>
              {allIds.map((pid) => <option key={pid} value={pid}>{pid}</option>)}
            </select>
          </label>
          <label className="space-y-1">
            <span className="lh-label">department</span>
            <DepartmentField value={department} options={departments} onChange={setDepartment} />
          </label>
        </div>
        <div className="space-y-1">
          <span className="lh-label">skills</span>
          <SkillPicker available={skills} selected={pickedSkills} onToggle={toggleSkill} />
        </div>
        <div className="space-y-1">
          <span className="lh-label">limits</span>
          <LimitsEditor rows={limitRows} setRows={setLimitRows} />
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            type="button" className="lh-btn lh-btn-primary" disabled={busy || !id.trim()}
            onClick={() => submit(false)}
          >
            Create brain
          </button>
          <button
            type="button" className="lh-btn" disabled={busy || !id.trim()}
            title="Create it and keep this form open (same parent + department) to add a sibling"
            onClick={() => submit(true)}
          >
            Create &amp; add another
          </button>
        </div>
      </div>
    </div>
  )
}
