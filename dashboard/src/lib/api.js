// The dashboard's API surface, in one import.
//
// `requireOk` (the status → Error translator) and the whole typed client live in
// `./apiClient`; this module re-exports them so the dozens of existing
// `import { requireOk } from '../lib/api'` call sites keep working verbatim while
// panels migrate to the typed functions.
//
//   import { getOverview, requireOk } from '../lib/api'
//   import * as api from '../lib/api'

export * from './apiClient'
