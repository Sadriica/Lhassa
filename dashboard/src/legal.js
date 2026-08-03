// The Appropriate Legal Notice — the dashboard's copy of lhassa/legal.py.
//
// Lhassa is AGPL-3.0-or-later with an additional term under section 7(b) of
// that license (see LICENSE at the repo root): this notice must be preserved
// and displayed in every user-facing interface that shows legal or version
// information. The dashboard's "about" card is that interface here.
//
// It is duplicated rather than fetched so the notice renders offline and with
// no loading flash. tests/test_legal_notice.py asserts this file and
// lhassa/legal.py still agree, so the two cannot drift apart and neither can be
// quietly emptied.

export const PROJECT = 'Lhassa'
export const VENDOR = 'Witara'
export const LICENSE_ID = 'AGPL-3.0-or-later'
export const SOURCE_URL = 'https://github.com/Sadriica/Lhassa'

export const ATTRIBUTION = `${PROJECT} — a ${VENDOR} project`
export const NOTICE = `${ATTRIBUTION}. Licensed under ${LICENSE_ID}.`
