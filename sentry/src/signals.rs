// Port of lhassa/org/monitor.py::gather_signals (episodes, health, flows, approvals,
// failures) with the SAME read-only sources and the SAME failure-detection rules.
// Every source is read defensively: a missing/unreadable file or table degrades to
// empty, mirroring the Python's per-source try/except.

use std::fs;
use std::path::Path;

use rusqlite::Connection;
use serde::Serialize;
use serde_json::Value;

use crate::pyconfig::SentryConfig;

const DEFAULT_EPISODES: i64 = 20;

// Substrings marking a failure / offline / deferred outcome - identical list and
// order-independence to lhassa.org.monitor._FAILURE_MARKERS.
const FAILURE_MARKERS: &[&str] = &[
    "error", "errored", "deferred", "offline", "could not", "failed", "fail", "timeout",
    "denied",
];

#[derive(Debug, Clone, Serialize, Default)]
pub struct Episode {
    pub id: String,
    pub despertar_por: String,
    pub decision: String,
    pub accion: String,
    pub resultado: String,
    pub costo_usd: f64,
    pub creado_en: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct Approval {
    pub id: String,
    pub tipo: String,
    pub estado: String,
    pub creado_en: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct Health {
    pub worst_level: String,
    pub ok: bool,
    pub failing: Vec<String>,
    pub alerted: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct Flow {
    pub id: String,
    pub name: String,
    pub nodes: usize,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct Failure {
    pub id: String,
    pub department: Option<String>,
    pub decision: String,
    pub accion: String,
    pub resultado: String,
    pub creado_en: String,
}

#[derive(Debug, Clone, Serialize, Default)]
pub struct Signals {
    pub instance_id: String,
    pub episodes: Vec<Episode>,
    pub health: Health,
    pub flows: Vec<Flow>,
    pub approvals: Vec<Approval>,
    pub failures: Vec<Failure>,
    // agent_runs is deliberately always empty: the Python signal comes from
    // in-process asyncio state (lhassa.state.state.agent_runs), never persisted to
    // disk or DB, so a separate Rust process has no way to observe it. Kept as a
    // field (not omitted) so the shape matches the Python capture and callers can
    // see explicitly that it is empty rather than missing. See doc comment on
    // `detect_events` in events.rs for the consequence (run_started/run_finished
    // never fire from the sentry in v1).
    pub agent_runs: std::collections::BTreeMap<String, String>,
}

fn read_episodes(db_path: &Path, instance_id: &str, limit: i64) -> Vec<Episode> {
    if !db_path.exists() {
        return Vec::new();
    }
    let read = || -> rusqlite::Result<Vec<Episode>> {
        let conn = Connection::open_with_flags(
            db_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        let mut stmt = conn.prepare(
            "SELECT id, despertar_por, decision, accion, resultado, costo_usd, creado_en
               FROM episodes
              WHERE instance_id = ?1
              ORDER BY creado_en DESC, rowid DESC
              LIMIT ?2",
        )?;
        let rows = stmt.query_map(rusqlite::params![instance_id, limit], |row| {
            Ok(Episode {
                id: row.get::<_, Option<String>>(0)?.unwrap_or_default(),
                despertar_por: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                decision: row.get::<_, Option<String>>(2)?.unwrap_or_default(),
                accion: row.get::<_, Option<String>>(3)?.unwrap_or_default(),
                resultado: row.get::<_, Option<String>>(4)?.unwrap_or_default(),
                costo_usd: row.get::<_, Option<f64>>(5)?.unwrap_or(0.0),
                creado_en: row.get::<_, Option<String>>(6)?.unwrap_or_default(),
            })
        })?;
        rows.collect()
    };
    read().unwrap_or_default()
}

fn read_pending_approvals(db_path: &Path, instance_id: &str) -> Vec<Approval> {
    if !db_path.exists() {
        return Vec::new();
    }
    let read = || -> rusqlite::Result<Vec<Approval>> {
        let conn = Connection::open_with_flags(
            db_path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        let mut stmt = conn.prepare(
            "SELECT id, tipo, estado, creado_en
               FROM approvals
              WHERE instance_id = ?1 AND estado = 'pendiente'
              ORDER BY creado_en",
        )?;
        let rows = stmt.query_map(rusqlite::params![instance_id], |row| {
            Ok(Approval {
                id: row.get::<_, Option<String>>(0)?.unwrap_or_default(),
                tipo: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
                estado: row.get::<_, Option<String>>(2)?.unwrap_or_default(),
                creado_en: row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            })
        })?;
        rows.collect()
    };
    read().unwrap_or_default()
}

fn dept_from_episode(accion: &str) -> Option<String> {
    let marker = "dept:";
    let idx = accion.find(marker)?;
    let tail = &accion[idx + marker.len()..];
    let name = tail.split_whitespace().next().unwrap_or("").trim();
    if name.is_empty() {
        None
    } else {
        Some(name.to_string())
    }
}

/// Parses an ISO-8601 timestamp the same way Python's `datetime.fromisoformat`
/// does for the shapes the DB actually stores (with a trailing `Z` swapped for
/// `+00:00` first, exactly like the Python). Returns `None` on anything it can't
/// parse, which the caller treats as "keep the episode" (matches the Python's
/// fail-open behavior for staleness).
fn parse_iso(ts: &str) -> Option<i64> {
    // Minimal RFC3339-ish parser: YYYY-MM-DDTHH:MM:SS[.ffffff][+HH:MM|Z]
    let ts = ts.replace('Z', "+00:00");
    let (date_part, rest) = ts.split_once('T')?;
    let mut y_m_d = date_part.split('-');
    let year: i64 = y_m_d.next()?.parse().ok()?;
    let month: i64 = y_m_d.next()?.parse().ok()?;
    let day: i64 = y_m_d.next()?.parse().ok()?;

    // Split off the timezone offset (+HH:MM or -HH:MM), defaulting to UTC.
    let (time_part, offset_secs) = split_offset(rest);
    let mut hms = time_part.split(':');
    let hour: i64 = hms.next()?.parse().ok()?;
    let minute: i64 = hms.next()?.parse().ok()?;
    let sec_str = hms.next().unwrap_or("0");
    let second: f64 = sec_str.parse().ok()?;

    let days = days_from_civil(year, month, day);
    let epoch_secs = days * 86400 + hour * 3600 + minute * 60 + second as i64;
    Some(epoch_secs - offset_secs)
}

fn split_offset(time_part: &str) -> (&str, i64) {
    // Find a +HH:MM or -HH:MM suffix after any fractional seconds.
    if let Some(pos) = time_part.rfind('+') {
        let (t, off) = time_part.split_at(pos);
        return (t, parse_offset(off));
    }
    // A '-' could also be a negative offset, but only past the first 8 chars
    // (HH:MM:SS) to avoid mis-splitting the time itself.
    if time_part.len() > 8 {
        if let Some(pos) = time_part[8..].find('-') {
            let idx = 8 + pos;
            let (t, off) = time_part.split_at(idx);
            return (t, parse_offset(off));
        }
    }
    (time_part, 0)
}

fn parse_offset(off: &str) -> i64 {
    let sign = if off.starts_with('-') { -1 } else { 1 };
    let off = off.trim_start_matches(['+', '-']);
    let mut parts = off.split(':');
    let h: i64 = parts.next().and_then(|s| s.parse().ok()).unwrap_or(0);
    let m: i64 = parts.next().and_then(|s| s.parse().ok()).unwrap_or(0);
    sign * (h * 3600 + m * 60)
}

// Howard Hinnant's days-from-civil, days since 1970-01-01 (proleptic Gregorian).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn now_epoch_secs() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn is_stale(creado_en: &str, max_age_hours: f64) -> bool {
    if max_age_hours <= 0.0 {
        return false;
    }
    let Some(ts) = parse_iso(creado_en) else {
        return false; // unparseable timestamp counts as fresh, same as Python
    };
    let age_secs = (now_epoch_secs() - ts).max(0) as f64;
    age_secs > max_age_hours * 3600.0
}

fn failures_from_episodes(episodes: &[Episode], max_age_hours: f64) -> Vec<Failure> {
    let mut out = Vec::new();
    for ep in episodes {
        if ep.despertar_por == "monitor" {
            continue;
        }
        if is_stale(&ep.creado_en, max_age_hours) {
            continue;
        }
        let is_think = ep.decision.to_lowercase().starts_with("think");
        let blob = if is_think {
            format!("{} {}", ep.decision, ep.accion).to_lowercase()
        } else {
            format!("{} {} {}", ep.decision, ep.accion, ep.resultado).to_lowercase()
        };
        if FAILURE_MARKERS.iter().any(|m| blob.contains(m)) {
            out.push(Failure {
                id: ep.id.clone(),
                department: dept_from_episode(&ep.accion),
                decision: ep.decision.clone(),
                accion: ep.accion.clone(),
                resultado: ep.resultado.clone(),
                creado_en: ep.creado_en.clone(),
            });
        }
    }
    out
}

fn health_signal(health_dir: &Path) -> Health {
    let empty = Health { worst_level: "info".to_string(), ok: true, failing: vec![], alerted: vec![] };
    let path = health_dir.join("digest.json");
    let Ok(raw_text) = fs::read_to_string(&path) else {
        return empty;
    };
    let Ok(raw) = serde_json::from_str::<Value>(&raw_text) else {
        return empty;
    };
    let Some(obj) = raw.as_object() else {
        return empty;
    };
    let results = obj.get("results").and_then(|v| v.as_object());
    let mut failing = Vec::new();
    if let Some(results) = results {
        for (name, r) in results {
            if let Some(level) = r.get("level").and_then(|v| v.as_str()) {
                if level == "warn" || level == "alert" {
                    failing.push(name.clone());
                }
            }
        }
    }
    failing.sort();
    let worst_level = obj
        .get("worst_level")
        .and_then(|v| v.as_str())
        .unwrap_or("info")
        .to_string();
    let ok = obj.get("ok").and_then(|v| v.as_bool()).unwrap_or(true);
    let alerted = obj
        .get("alerted")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    Health { worst_level, ok, failing, alerted }
}

fn flows_signal(flows_file: &Path) -> Vec<Flow> {
    let Ok(raw_text) = fs::read_to_string(flows_file) else {
        return Vec::new();
    };
    let Ok(raw) = serde_json::from_str::<Value>(&raw_text) else {
        return Vec::new();
    };
    let Some(obj) = raw.as_object() else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (fid, data) in obj {
        let Some(data) = data.as_object() else { continue };
        let id = data
            .get("id")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or(fid)
            .to_string();
        let name = data
            .get("name")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or(&id)
            .to_string();
        let nodes = data.get("nodes").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
        out.push(Flow { id, name, nodes });
    }
    out
}

/// Port of `lhassa.org.monitor.gather_signals`, same sources and same shape (minus
/// `agent_runs`, always empty here; see the field's doc comment on `Signals`).
pub fn gather_signals(config: &SentryConfig) -> Signals {
    let episodes = read_episodes(&config.brain_db_path, &config.instance_id, DEFAULT_EPISODES);
    let failures = failures_from_episodes(&episodes, config.monitor_failure_window_h);
    let health = health_signal(&config.health_dir);
    let flows = flows_signal(&config.flows_file);
    let approvals = read_pending_approvals(&config.brain_db_path, &config.instance_id);
    Signals {
        instance_id: config.instance_id.clone(),
        episodes,
        health,
        flows,
        approvals,
        failures,
        agent_runs: std::collections::BTreeMap::new(),
    }
}

#[cfg(test)]
mod fixture_tests {
    use super::*;
    use crate::pyconfig::SentryConfig;
    use std::fs;

    // A throwaway brain.db with the same schema as lhassa/core/db.py, populated
    // with INVENTED episodes/approvals only (doc/boundaries.md: fixtures use
    // invented identities, never real ones).
    fn make_test_db(dir: &std::path::Path) -> std::path::PathBuf {
        let db_path = dir.join("brain.db");
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE episodes (
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, creado_en TEXT NOT NULL,
                despertar_por TEXT, decision TEXT, accion TEXT, resultado TEXT,
                dominio TEXT, costo_usd REAL, memoria_cargada TEXT, raw TEXT
            );
            CREATE TABLE approvals (
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, tipo TEXT, payload TEXT,
                estado TEXT NOT NULL DEFAULT 'pendiente', creado_en TEXT NOT NULL, resuelto_en TEXT
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO episodes (id, instance_id, creado_en, despertar_por, decision, accion, resultado, costo_usd)
             VALUES ('ep_1', 'inst_test', '2026-07-24T09:00:00+00:00', 'task:send_digest', 'send_digest', 'send_digest', 'send_digest: failed to deliver', 0.0)",
            [],
        ).unwrap();
        conn.execute(
            "INSERT INTO episodes (id, instance_id, creado_en, despertar_por, decision, accion, resultado, costo_usd)
             VALUES ('ep_2', 'inst_test', '2026-07-24T09:05:00+00:00', 'monitor', 'monitor: watch', 'monitor:interns', '1 new failure(s): x', 0.0)",
            [],
        ).unwrap();
        conn.execute(
            "INSERT INTO approvals (id, instance_id, tipo, estado, creado_en)
             VALUES ('ap_1', 'inst_test', 'org_run', 'pendiente', '2026-07-24T09:10:00+00:00')",
            [],
        ).unwrap();
        conn.execute(
            "INSERT INTO approvals (id, instance_id, tipo, estado, creado_en, resuelto_en)
             VALUES ('ap_2', 'inst_test', 'org_run', 'aprobado', '2026-07-24T09:11:00+00:00', '2026-07-24T09:12:00+00:00')",
            [],
        ).unwrap();
        db_path
    }

    #[test]
    fn read_episodes_returns_newest_first_for_this_instance() {
        let dir = tempdir();
        let db_path = make_test_db(dir.path());
        let eps = read_episodes(&db_path, "inst_test", 20);
        assert_eq!(eps.len(), 2);
        assert_eq!(eps[0].id, "ep_2"); // newest creado_en first
        assert_eq!(eps[1].id, "ep_1");
    }

    #[test]
    fn read_pending_approvals_excludes_resolved() {
        let dir = tempdir();
        let db_path = make_test_db(dir.path());
        let aps = read_pending_approvals(&db_path, "inst_test");
        assert_eq!(aps.len(), 1);
        assert_eq!(aps[0].id, "ap_1");
    }

    #[test]
    fn missing_db_degrades_to_empty_not_panic() {
        let dir = tempdir();
        let missing = dir.path().join("nope.db");
        assert!(read_episodes(&missing, "inst_test", 20).is_empty());
        assert!(read_pending_approvals(&missing, "inst_test").is_empty());
    }

    #[test]
    fn health_signal_reads_digest_and_computes_failing() {
        let dir = tempdir();
        fs::create_dir_all(dir.path()).unwrap();
        let digest = dir.path().join("digest.json");
        fs::write(
            &digest,
            r#"{"worst_level":"warn","ok":false,
                "results":{"disk":{"level":"info"},"git_status":{"level":"warn"}},
                "alerted":["git_status"]}"#,
        )
        .unwrap();
        let h = health_signal(dir.path());
        assert_eq!(h.worst_level, "warn");
        assert!(!h.ok);
        assert_eq!(h.failing, vec!["git_status".to_string()]);
        assert_eq!(h.alerted, vec!["git_status".to_string()]);
    }

    #[test]
    fn health_signal_missing_file_degrades_to_info() {
        let dir = tempdir();
        let h = health_signal(dir.path());
        assert_eq!(h.worst_level, "info");
        assert!(h.ok);
        assert!(h.failing.is_empty());
    }

    #[test]
    fn flows_signal_reads_id_name_and_node_count() {
        let dir = tempdir();
        let flows_file = dir.path().join("flows.json");
        fs::write(
            &flows_file,
            r#"{"flow-abc":{"id":"flow-abc","name":"Invented flow","nodes":[{"id":"n1"},{"id":"n2"}]}}"#,
        )
        .unwrap();
        let flows = flows_signal(&flows_file);
        assert_eq!(flows.len(), 1);
        assert_eq!(flows[0].id, "flow-abc");
        assert_eq!(flows[0].name, "Invented flow");
        assert_eq!(flows[0].nodes, 2);
    }

    #[test]
    fn gather_signals_end_to_end_matches_python_shape() {
        let dir = tempdir();
        let db_path = make_test_db(dir.path());
        let health_dir = dir.path().join("health");
        fs::create_dir_all(&health_dir).unwrap();
        let flows_file = dir.path().join("flows.json");
        fs::write(&flows_file, "{}").unwrap();

        let config = SentryConfig::for_test(db_path, health_dir, flows_file);
        let signals = gather_signals(&config);

        assert_eq!(signals.episodes.len(), 2);
        assert_eq!(signals.approvals.len(), 1);
        // ep_2 is the monitor's own episode and must never count as a failure;
        // ep_1 mentions "failed" and must.
        assert_eq!(signals.failures.len(), 1);
        assert_eq!(signals.failures[0].id, "ep_1");
        assert!(signals.agent_runs.is_empty());
    }

    // Minimal tempdir helper (no external crate) - a unique dir under std::env::temp_dir.
    struct TempDir(std::path::PathBuf);
    impl TempDir {
        fn path(&self) -> &std::path::Path {
            &self.0
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn tempdir() -> TempDir {
        let mut p = std::env::temp_dir();
        let unique = format!(
            "lhassa-sentry-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        p.push(unique);
        fs::create_dir_all(&p).unwrap();
        TempDir(p)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_iso_with_z_and_offset_the_same() {
        let a = parse_iso("2026-07-24T10:00:00+00:00").unwrap();
        let b = parse_iso("2026-07-24T10:00:00Z").unwrap();
        assert_eq!(a, b);
    }

    #[test]
    fn parses_iso_with_microseconds() {
        let a = parse_iso("2026-07-24T10:00:00.123456+00:00").unwrap();
        let b = parse_iso("2026-07-24T10:00:00+00:00").unwrap();
        // Truncated to whole seconds in our integer epoch; both land on the same second.
        assert_eq!(a, b);
    }

    #[test]
    fn unparseable_timestamp_is_never_stale() {
        assert!(!is_stale("not-a-timestamp", 24.0));
    }

    #[test]
    fn zero_window_never_stale() {
        assert!(!is_stale("2000-01-01T00:00:00Z", 0.0));
    }

    #[test]
    fn old_timestamp_is_stale_with_window() {
        assert!(is_stale("2000-01-01T00:00:00Z", 24.0));
    }

    #[test]
    fn failures_skip_monitor_own_episodes() {
        let episodes = vec![Episode {
            id: "e1".into(),
            despertar_por: "monitor".into(),
            decision: "monitor: watch".into(),
            accion: "monitor:interns".into(),
            resultado: "1 new failure(s): x".into(),
            costo_usd: 0.0,
            creado_en: "2026-07-24T10:00:00Z".into(),
        }];
        assert!(failures_from_episodes(&episodes, 24.0).is_empty());
    }

    #[test]
    fn failures_from_think_only_match_decision_and_accion() {
        // A think whose PROSE result contains "error"/"fail" must NOT count.
        let episodes = vec![Episode {
            id: "e1".into(),
            despertar_por: "task:cv".into(),
            decision: "think: draft cv".into(),
            accion: "draft_cv".into(),
            resultado: "the candidate failed to include an error-handling section".into(),
            costo_usd: 0.01,
            creado_en: "2026-07-24T10:00:00Z".into(),
        }];
        assert!(failures_from_episodes(&episodes, 24.0).is_empty());
    }

    #[test]
    fn failures_from_non_think_match_resultado_too() {
        let episodes = vec![Episode {
            id: "e1".into(),
            despertar_por: "task:send_digest".into(),
            decision: "send_digest".into(),
            accion: "send_digest".into(),
            resultado: "send_digest: failed to deliver".into(),
            costo_usd: 0.0,
            creado_en: "2026-07-24T10:00:00Z".into(),
        }];
        assert_eq!(failures_from_episodes(&episodes, 24.0).len(), 1);
    }

    #[test]
    fn dept_from_episode_extracts_marker() {
        assert_eq!(dept_from_episode("dept:operations do the thing"), Some("operations".to_string()));
        assert_eq!(dept_from_episode("no marker here"), None);
    }
}
