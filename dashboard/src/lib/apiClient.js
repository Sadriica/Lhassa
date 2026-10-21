// The dashboard's ONE door to the brain's HTTP API.
//
// Before this module every panel hand-rolled `fetch('/api/…')` and each one
// decided for itself whether to check the status, how to word the error, and
// whether to send credentials. That is three inconsistencies per call site and
// no single place to add an auth header. This module centralises:
//
//   * the base URL         ; `API_BASE`, same-origin by default (the API serves
//                             the dashboard), overridable at build time with
//                             `VITE_LHASSA_API_BASE` for a split deployment;
//   * the auth header      ; `require_local_or_token` (lhassa/api/server.py)
//                             accepts `Authorization: Bearer <t>` or
//                             `X-Lhassa-Api-Token: <t>` and waives both on
//                             loopback. We send the token when one is
//                             configured (`VITE_LHASSA_API_TOKEN`), so the same
//                             build works behind a token without edits;
//   * status → Error       ; via `requireOk`, the existing helper, so error
//                             wording stays identical to what panels showed;
//   * JSON parsing         ; one place that tolerates an empty 200 body.
//
// "Typed" here means JSDoc: this is a plain-JS project (no tsconfig), so the
// annotations buy editor completion and a readable contract without adding a
// build step. Each function documents its params and its return shape.
//
// Every function returns a Promise of parsed JSON and REJECTS with an Error on a
// non-2xx; callers `try/catch` exactly as they already did around `requireOk`.

/**
 * Turn a non-2xx response into a worded Error; pass a 2xx through unchanged.
 * Re-exported from `./api` for the many panels that import it from there.
 * @param {Response} response
 * @param {string} [label] what the user was trying to load, for the message
 * @returns {Promise<Response>}
 * @throws {Error}
 */
export async function requireOk(response, label = 'request') {
  if (response.ok) return response
  if (response.status === 404) {
    throw new Error(`${label} endpoint is missing. Restart the backend so the latest routes are loaded.`)
  }
  if (response.status >= 500) {
    throw new Error(`${label} backend error. Check the API logs or database path.`)
  }
  throw new Error(`${label} failed (${response.status}).`)
}

/**
 * The API origin. Empty string = same origin (the normal, loopback deployment).
 * @type {string}
 */
export const API_BASE = (import.meta.env?.VITE_LHASSA_API_BASE || '').replace(/\/+$/, '')

/**
 * The API token, when this build talks to a non-loopback brain.
 * @type {string}
 */
const API_TOKEN = import.meta.env?.VITE_LHASSA_API_TOKEN || ''

/**
 * Build a full URL from a path and an optional query object.
 * `null`/`undefined`/`''` query values are omitted rather than sent empty.
 * @param {string} path e.g. `/api/flows`
 * @param {Record<string, string|number|boolean|null|undefined>} [query]
 * @returns {string}
 */
export function apiUrl(path, query) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query || {})) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return `${API_BASE}${path}${qs ? `?${qs}` : ''}`
}

/**
 * Headers for a request, carrying the API token when one is configured.
 * @param {boolean} [json] whether a JSON body is being sent
 * @returns {Record<string, string>}
 */
function headers(json) {
  /** @type {Record<string, string>} */
  const out = {}
  if (json) out['Content-Type'] = 'application/json'
  if (API_TOKEN) out['X-Lhassa-Api-Token'] = API_TOKEN
  return out
}

/**
 * The one request primitive. Every exported function below routes through it.
 * @param {string} path
 * @param {{method?: string, query?: object, body?: unknown, label?: string}} [options]
 * @returns {Promise<any>} the parsed JSON body (`null` for an empty 2xx body)
 * @throws {Error} on a non-2xx response, worded by `requireOk`
 */
export async function request(path, options = {}) {
  const { method = 'GET', query, body, label } = options
  const init = { method, headers: headers(body !== undefined) }
  if (body !== undefined) init.body = JSON.stringify(body)
  const response = await fetch(apiUrl(path, query), init)
  if (!response.ok) {
    // The API words its own 4xx (`{"detail": "..."}`; e.g. a flow that failed
    // validation names the offending node). Prefer that over a generic message:
    // it is the difference between "Flow failed (400)" and "duplicate node id
    // 'gate'". Fall back to `requireOk`'s wording when there is no detail.
    const detail = await response
      .clone()
      .json()
      .then((d) => (d && typeof d.detail === 'string' ? d.detail : ''))
      .catch(() => '')
    if (detail) throw new Error(detail)
    await requireOk(response, label || path)
  }
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`${label || path} returned a malformed response.`)
  }
}

/** @param {string} v @returns {string} */
const enc = (v) => encodeURIComponent(String(v))

// ── runtime / status ─────────────────────────────────────────────────────────

/** @returns {Promise<any>} the `/api/status` snapshot */
export const getStatus = () => request('/api/status', { label: 'Status' })

/** @returns {Promise<any>} the composite overview (signals, approvals, runs, …) */
export const getOverview = () => request('/api/overview', { label: 'Overview' })

/** @returns {Promise<{packs: {name: string, summary: string}[]}>} the loaded capability packs */
export const getPacks = () => request('/api/packs', { label: 'Packs' })

/** @returns {Promise<any>} the health digest */
export const getHealth = () => request('/api/health', { label: 'Health' })

/** @returns {Promise<any[]>} live/recent agent rows */
export const getAgents = () => request('/api/agents', { label: 'Agents' })

/** @returns {Promise<any>} the live metrics snapshot */
export const getMetrics = () => request('/api/metrics', { label: 'Metrics' })

/** @returns {Promise<any[]>} the audit trail */
export const getAudit = () => request('/api/audit', { label: 'Audit' })

/** @param {number} [n] @returns {Promise<any>} spend/report rows */
export const getReports = (n = 50) => request('/api/reports', { query: { n }, label: 'Reports' })

/** @returns {Promise<any>} cost projections */
export const getProjections = () => request('/api/projections', { label: 'Projections' })

/** @returns {Promise<any[]>} scheduled tasks */
export const getScheduled = () => request('/api/scheduled', { label: 'Scheduled' })

/** @returns {Promise<any>} the fleet/readiness view */
export const getFleet = () => request('/api/fleet', { label: 'Fleet' })

// ── llm ledger ───────────────────────────────────────────────────────────────

/** @returns {Promise<any>} the spend ledger */
export const getLlmLedger = () => request('/api/llm/ledger', { label: 'AI usage' })

/** @returns {Promise<any>} the configured budget limits */
export const getLlmLimits = () => request('/api/llm/limits', { label: 'AI limits' })

/** @param {object} body @returns {Promise<any>} */
export const setLlmLimits = (body) =>
  request('/api/llm/limits', { method: 'POST', body, label: 'AI limits' })

/** @returns {Promise<any>} subscription-CLI usage */
export const getCcusage = () => request('/api/llm/ccusage', { label: 'CLI usage' })

// ── analytics ────────────────────────────────────────────────────────────────

/** @param {string} [range] @returns {Promise<any>} */
export const getAnalyticsSummary = (range = '30d') =>
  request('/api/analytics/summary', { query: { range }, label: 'Analytics' })

/** @param {string} [range] @returns {Promise<any>} */
export const getAnalyticsFlows = (range = '30d') =>
  request('/api/analytics/flows', { query: { range }, label: 'Flow analytics' })

/** @param {string} [range] @returns {Promise<any>} */
export const getAnalyticsBrains = (range = '30d') =>
  request('/api/analytics/brains', { query: { range }, label: 'Brain analytics' })

/** @param {{kind?: string, entity_id?: string, limit?: number}} [query] @returns {Promise<any>} */
export const getAnalyticsRuns = (query = {}) =>
  request('/api/analytics/runs', { query, label: 'Runs' })

/** @returns {Promise<any>} the inferred "am I landing?" feedback signal */
export const getFeedback = () => request('/api/analytics/feedback', { label: 'Feedback' })

// ── approvals ────────────────────────────────────────────────────────────────

/** @returns {Promise<any[]>} pending approvals, each already `describe()`d */
export const getApprovals = () => request('/api/approvals', { label: 'Approvals' })

/**
 * Resolve one approval through `security/approval.py:resolve`.
 * @param {string} id
 * @param {boolean} approved
 * @returns {Promise<any>}
 */
export const resolveApproval = (id, approved) =>
  request(`/api/approvals/${enc(id)}`, { method: 'POST', body: { approved }, label: 'Approval' })

// ── memory (facts, sessions, the knowledge graph) ────────────────────────────

/** @returns {Promise<any>} the stored facts */
export const getFacts = () => request('/api/memory/facts', { label: 'Memory' })

/** @returns {Promise<any>} the legacy voice-session store */
export const getSessions = () => request('/api/memory/sessions', { label: 'Sessions' })

/**
 * The $0 knowledge-graph read model for one entity.
 * @param {string} q
 * @returns {Promise<{query: string, facts: any[], episodes: any[], related_terms: string[],
 *                    first_seen: string|null, last_seen: string|null}>}
 */
export const getMemoryGraph = (q) =>
  request('/api/memory/graph', { query: { q }, label: 'Memory graph' })

/**
 * Every entity the brain has extracted, by mention count.
 * @returns {Promise<{entities: {term: string, mentions: number, last_seen: string, source: string}[]}>}
 */
export const getMemoryEntities = () => request('/api/memory/entities', { label: 'Entities' })

// ── episodes (FTS5 recall) ───────────────────────────────────────────────────

/**
 * BM25-ranked recall over the episode log (layer 5).
 * @param {string} q
 * @param {number} [n]
 * @returns {Promise<{query: string, count: number, results: any[]}>}
 */
export const searchEpisodes = (q, n = 20) =>
  request('/api/episodes/search', { query: { q, n }, label: 'Episode search' })

// ── projects ─────────────────────────────────────────────────────────────────

/** @param {boolean} [all] include archived @returns {Promise<any[]>} */
export const getProjects = (all = false) =>
  request('/api/projects', { query: all ? { all: 1 } : {}, label: 'Projects' })

/** @param {string} id @returns {Promise<any>} */
export const getProject = (id) => request(`/api/projects/${enc(id)}`, { label: 'Project' })

/** @param {string} id @returns {Promise<any[]>} */
export const getProjectRuns = (id) =>
  request(`/api/projects/${enc(id)}/runs`, { label: 'Project runs' })

/** @param {string} id @returns {Promise<any[]>} */
export const getProjectArtifacts = (id) =>
  request(`/api/projects/${enc(id)}/artifacts`, { label: 'Project artifacts' })

/** @param {string} id @param {number} [limit] @returns {Promise<any[]>} FTS5 episode rows */
export const getProjectEpisodes = (id, limit = 20) =>
  request(`/api/projects/${enc(id)}/episodes`, { query: { limit }, label: 'Project episodes' })

// ── artifacts (the delivery chain's up-direction) ────────────────────────────

/** @param {number} [limit] @returns {Promise<any[]>} artifact rows with decoded paths/metadata */
export const getArtifacts = (limit = 50) =>
  request('/api/artifacts', { query: { limit }, label: 'Artifacts' })

/** @param {string} id @returns {Promise<any>} */
export const getArtifact = (id) => request(`/api/artifacts/${enc(id)}`, { label: 'Artifact' })

// ── integrity (the conformance manifest) ─────────────────────────────────────

/**
 * @returns {Promise<{manifest_version: any, attestation: any, state: any,
 *                    contracts: {id: string, version: string, severity: string, description: string}[]}>}
 */
export const getIntegrity = () => request('/api/integrity', { label: 'Integrity' })

/** @returns {Promise<any>} run the integrity check now */
export const runIntegrityCheck = () =>
  request('/api/integrity/check', { method: 'POST', label: 'Integrity check' })

/** @param {number} [limit] @returns {Promise<any[]>} */
export const getIntegrityFindings = (limit = 100) =>
  request('/api/integrity/findings', { query: { limit }, label: 'Integrity findings' })

// ── research (idea assessments) ──────────────────────────────────────────────

/** @param {{status?: string, limit?: number}} [query] @returns {Promise<any[]>} */
export const getResearchAssessments = (query = {}) =>
  request('/api/research/assessments', { query, label: 'Research assessments' })

/** @param {string} id @returns {Promise<any>} the assessment row plus its `events` */
export const getResearchAssessment = (id) =>
  request(`/api/research/assessments/${enc(id)}`, { label: 'Research assessment' })

// ── flows (definitions) ──────────────────────────────────────────────────────

/** @returns {Promise<any[]>} */
export const getFlows = () => request('/api/flows', { label: 'Flows' })

/** @returns {Promise<any[]>} the flow-templates gallery */
export const getFlowTemplates = () => request('/api/flows/templates', { label: 'Flow templates' })

/** @param {string} name @returns {Promise<any>} */
export const applyFlowTemplate = (name) =>
  request(`/api/flows/templates/${enc(name)}/apply`, { method: 'POST', label: 'Flow template' })

/** @param {string} id @returns {Promise<any>} */
export const getFlow = (id) => request(`/api/flows/${enc(id)}`, { label: 'Flow' })

/** @param {object} flow @returns {Promise<any>} */
export const createFlow = (flow) =>
  request('/api/flows', { method: 'POST', body: flow, label: 'Flow' })

/** @param {string} id @param {object} flow @returns {Promise<any>} */
export const replaceFlow = (id, flow) =>
  request(`/api/flows/${enc(id)}`, { method: 'PUT', body: flow, label: 'Flow' })

/** @param {string} id @param {object} patch @returns {Promise<any>} */
export const patchFlow = (id, patch) =>
  request(`/api/flows/${enc(id)}`, { method: 'PATCH', body: patch, label: 'Flow' })

/** @param {string} id @returns {Promise<any>} */
export const deleteFlow = (id) =>
  request(`/api/flows/${enc(id)}`, { method: 'DELETE', label: 'Flow' })

/**
 * Run a stored flow. The server routes it through `org/delegate.py:delegate_flow`
 *; the dashboard never reaches the engine directly.
 * @param {string} id
 * @param {{inputs?: object, input?: string, company?: string, idempotency_key?: string}} [body]
 * @returns {Promise<any>} the run record
 */
export const runFlow = (id, body = {}) =>
  request(`/api/flows/${enc(id)}/run`, { method: 'POST', body, label: 'Flow run' })

// ── flow runs (the durable kernel) ───────────────────────────────────────────

/** @param {{status?: string, limit?: number}} [query] @returns {Promise<any[]>} */
export const getFlowRuns = (query = {}) =>
  request('/api/flow-runs', { query, label: 'Flow runs' })

/** @param {string} runId @returns {Promise<any>} the public run record */
export const getFlowRun = (runId) =>
  request(`/api/flow-runs/${enc(runId)}`, { label: 'Flow run' })

/** @param {string} runId @returns {Promise<any[]>} the append-only event stream */
export const getFlowRunEvents = (runId) =>
  request(`/api/flow-runs/${enc(runId)}/events`, { label: 'Flow run events' })

// ── skills ───────────────────────────────────────────────────────────────────

/** @returns {Promise<any[]>} */
export const getSkills = () => request('/api/skills', { label: 'Skills' })

/** @param {string} context @returns {Promise<any>} */
export const recommendSkills = (context) =>
  request('/api/skills/recommend', { query: { context }, label: 'Skill recommendations' })

/** @param {object} body @returns {Promise<any>} */
export const createSkill = (body) =>
  request('/api/skills', { method: 'POST', body, label: 'Skill' })

/** @param {object} body @returns {Promise<any>} */
export const createSkillWithAi = (body) =>
  request('/api/skills/ai', { method: 'POST', body, label: 'Skill' })

/** @param {string} name @param {object} body @returns {Promise<any>} */
export const improveSkill = (name, body) =>
  request(`/api/skills/${enc(name)}/improve`, { method: 'POST', body, label: 'Skill' })

/** @param {string} name @returns {Promise<any>} */
export const deleteSkill = (name) =>
  request(`/api/skills/${enc(name)}`, { method: 'DELETE', label: 'Skill' })

/** @param {string} name @param {object} body @returns {Promise<any>} */
export const assignSkill = (name, body) =>
  request(`/api/skills/${enc(name)}/assign`, { method: 'POST', body, label: 'Skill' })

/** @param {string} name @param {object} [body] @returns {Promise<any>} */
export const installSkill = (name, body = {}) =>
  request(`/api/skills/${enc(name)}/install`, { method: 'POST', body, label: 'Skill' })

/** @param {string} name @returns {Promise<any[]>} */
export const getSkillBrains = (name) =>
  request(`/api/skills/${enc(name)}/brains`, { label: 'Skill brains' })

// ── org (topology, brains, relationships, templates) ─────────────────────────

/** @returns {Promise<any>} */
export const getTopology = () => request('/api/org/topology', { label: 'Topology' })

/** @returns {Promise<any[]>} the specialist-domain catalog */
export const getOrgSkills = () => request('/api/org/skills', { label: 'Org skills' })

/** @param {object} body @returns {Promise<any>} */
export const createBrain = (body) =>
  request('/api/org/brains', { method: 'POST', body, label: 'Brain' })

/** @param {string} id @param {object} body @returns {Promise<any>} */
export const patchBrain = (id, body) =>
  request(`/api/org/brains/${enc(id)}`, { method: 'PATCH', body, label: 'Brain' })

/** @param {string} id @param {object} body @returns {Promise<any>} */
export const moveBrain = (id, body) =>
  request(`/api/org/brains/${enc(id)}/move`, { method: 'POST', body, label: 'Brain' })

/** @param {string} id @param {object} [body] @returns {Promise<any>} */
export const promoteBrain = (id, body = {}) =>
  request(`/api/org/brains/${enc(id)}/promote`, { method: 'POST', body, label: 'Brain' })

/** @param {string} id @param {boolean} [force] @returns {Promise<any>} */
export const deleteBrain = (id, force = false) =>
  request(`/api/org/brains/${enc(id)}`, { method: 'DELETE', query: { force }, label: 'Brain' })

/** @returns {Promise<any[]>} */
export const getRelationships = () =>
  request('/api/org/relationships', { label: 'Relationships' })

/** @returns {Promise<any>} the closed edge-kind vocabulary */
export const getRelationshipKinds = () =>
  request('/api/org/relationships/kinds', { label: 'Relationship kinds' })

/** @param {object} body @returns {Promise<any>} */
export const addRelationship = (body) =>
  request('/api/org/relationships', { method: 'POST', body, label: 'Relationship' })

/** @param {object} body @returns {Promise<any>} */
export const removeRelationship = (body) =>
  request('/api/org/relationships', { method: 'DELETE', body, label: 'Relationship' })

/** @param {string} goal @returns {Promise<any>} the deterministic routing verdict */
export const routeGoal = (goal) =>
  request('/api/org/route', { query: { goal }, label: 'Routing' })

/** @returns {Promise<any[]>} */
export const getOrgTemplates = () => request('/api/org/templates', { label: 'Org templates' })

/** @param {string} id @returns {Promise<any>} */
export const applyOrgTemplate = (id) =>
  request(`/api/org/templates/${enc(id)}/apply`, { method: 'POST', label: 'Org template' })

// ── tools (self-written, gate-graduated) ─────────────────────────────────────

/** @returns {Promise<any[]>} */
export const getTools = () => request('/api/tools', { label: 'Actions' })

/** @param {string} name @returns {Promise<any>} 409 if the gates did not pass */
export const graduateTool = (name) =>
  request(`/api/tools/${enc(name)}/graduate`, { method: 'POST', label: 'Tool graduation' })

// ── code (the agentic local-CLI door) ────────────────────────────────────────

/** @returns {Promise<any>} installed CLIs + the auto-detected default */
export const getCodeClis = () => request('/api/code/clis', { label: 'CLIs' })

/** @returns {Promise<any[]>} */
export const getCodeRuns = () => request('/api/code/runs', { label: 'Runs' })

/** @param {{task: string, cli?: string}} body @returns {Promise<any>} */
export const startCodeRun = (body) =>
  request('/api/code', { method: 'POST', body, label: 'Run' })

/** @param {string} id @returns {Promise<any>} */
export const stopCodeRun = (id) =>
  request(`/api/code/${enc(id)}/stop`, { method: 'POST', label: 'Run' })

// ── calendar ─────────────────────────────────────────────────────────────────

/** @param {number} [hours] @returns {Promise<any[]>} */
export const getCalendarEvents = (hours = 24) =>
  request('/api/calendar/events', { query: { hours }, label: 'Calendar' })

/** @returns {Promise<any[]>} */
export const getCalendars = () => request('/api/calendar/calendars', { label: 'Calendars' })

/** @param {object} body @returns {Promise<any>} */
export const addCalendar = (body) =>
  request('/api/calendar/calendars', { method: 'POST', body, label: 'Calendar' })

/** @param {string} name @returns {Promise<any>} */
export const removeCalendar = (name) =>
  request(`/api/calendar/calendars/${enc(name)}`, { method: 'DELETE', label: 'Calendar' })

/** @param {object} body @returns {Promise<any>} */
export const setPrimaryCalendar = (body) =>
  request('/api/calendar/primary', { method: 'POST', body, label: 'Calendar' })

/** @returns {Promise<any>} */
export const syncCalendar = () =>
  request('/api/calendar/sync', { method: 'POST', label: 'Calendar sync' })

// ── config (the settings service) ────────────────────────────────────────────

/** @returns {Promise<any>} the read model: value + default + overridden + restart hint */
export const getConfig = () => request('/api/config', { label: 'Settings' })

/** @param {object} body @returns {Promise<any>} */
export const patchConfig = (body) =>
  request('/api/config', { method: 'PATCH', body, label: 'Settings' })

export const getApprovalBriefs = () => request('/api/approvals/briefs', { label: 'Approval briefs' })
