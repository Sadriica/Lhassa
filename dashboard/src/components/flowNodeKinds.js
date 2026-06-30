// Per-kind vocabulary for the work-flow canvas: the CLOSED set of node kinds
// (trigger → task → gate → output, plus branch) with on-palette styling, a glyph,
// a one-line hint, and the default `config` a freshly-dropped node starts with.
//
// Colors stay on the Lhasa palette: trigger = sage green (the entry / "go"),
// task = temple gold (the brand — the working brains), gate = monk-robe red (a
// checkpoint, used sparingly), output = muted stone (a quiet sink), branch = a
// muted plum (a fork). Each kind exposes an `rgb` triple so a node can tint its
// border/fill at several opacities without hard-coding colors twice.

export const KIND_ORDER = ['trigger', 'task', 'gate', 'output', 'branch']

export const KINDS = {
  trigger: {
    kind: 'trigger',
    label: 'Trigger',
    glyph: '▷',
    rgb: '124,154,91', // sage green
    hint: 'the single entry point — manual, schedule, or event',
    hasTarget: false, // nothing may point back into the trigger
    hasSource: true,
    defaultConfig: { type: 'manual' },
  },
  task: {
    kind: 'task',
    label: 'Task',
    glyph: '◇',
    rgb: '217,165,33', // temple gold
    hint: 'work a brain does — auto by skill, or pin a brain',
    hasTarget: true,
    hasSource: true,
    defaultConfig: {},
  },
  gate: {
    kind: 'gate',
    label: 'Gate',
    glyph: '⛉',
    rgb: '162,62,52', // monk-robe red
    hint: 'a checkpoint — verify an artifact or park for approval',
    hasTarget: true,
    hasSource: true,
    defaultConfig: { type: 'verify', spec: {} },
  },
  output: {
    kind: 'output',
    label: 'Output',
    glyph: '⤓',
    rgb: '138,130,118', // muted stone
    hint: 'a sink — notify, write a doc, or log',
    hasTarget: true,
    hasSource: false,
    defaultConfig: { type: 'log' },
  },
  branch: {
    kind: 'branch',
    label: 'Branch',
    glyph: '⑂',
    rgb: '150,112,164', // muted plum
    hint: 'a fork — the condition lives on each outgoing edge (when)',
    hasTarget: true,
    hasSource: true,
    defaultConfig: {},
  },
}

export function kindMeta(kind) {
  return KINDS[kind] || KINDS.task
}

// A fresh, unique node id for a kind (e.g. `task-3`). Caller passes the ids already
// in use so we never collide even after deletes + re-adds.
export function freshNodeId(kind, usedIds) {
  const used = usedIds instanceof Set ? usedIds : new Set(usedIds || [])
  let i = 1
  let id = `${kind}-${i}`
  while (used.has(id)) {
    i += 1
    id = `${kind}-${i}`
  }
  return id
}
