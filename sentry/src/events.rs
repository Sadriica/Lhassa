// Port of lhassa/org/watcher.py's pure, deterministic diff: detect_events,
// _is_important, _fingerprint. No I/O here, same as the Python.

use serde::Serialize;
use std::collections::{BTreeMap, HashSet};

use crate::signals::Signals;

pub const KIND_FAILURES: &str = "failures_new";
pub const KIND_HEALTH: &str = "health_worsened";
pub const KIND_APPROVALS: &str = "approvals_new";
pub const KIND_RUN_STARTED: &str = "run_started";
pub const KIND_RUN_FINISHED: &str = "run_finished";

fn health_order(level: &str) -> i32 {
    match level {
        "info" | "ok" => 0,
        "warn" => 1,
        "alert" => 2,
        _ => 0,
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Event {
    pub kind: String,
    pub level: String,
    pub summary: String,
}

/// Diffs two `agent_runs` snapshots (`{run_id: status}`) into start/finish events,
/// same rule as `_diff_runs`. In the sentry both snapshots are always empty (see
/// `Signals::agent_runs` doc comment), so this never actually fires today; kept
/// faithful to the Python so the day agent_runs gains a persisted source, wiring
/// it in is a data problem, not a logic rewrite.
fn diff_runs(prev_runs: &BTreeMap<String, String>, curr_runs: &BTreeMap<String, String>) -> Vec<Event> {
    const RUNNING: &str = "running";
    let mut events = Vec::new();
    for (rid, status) in curr_runs {
        let running = status == RUNNING;
        match prev_runs.get(rid) {
            None => {
                if running {
                    events.push(Event {
                        kind: KIND_RUN_STARTED.to_string(),
                        level: "info".to_string(),
                        summary: format!("run {} started", rid),
                    });
                } else {
                    events.push(Event {
                        kind: KIND_RUN_FINISHED.to_string(),
                        level: "info".to_string(),
                        summary: format!("run {} finished ({})", rid, status),
                    });
                }
            }
            Some(prev_status) => {
                let was_running = prev_status == RUNNING;
                if was_running && !running {
                    events.push(Event {
                        kind: KIND_RUN_FINISHED.to_string(),
                        level: "info".to_string(),
                        summary: format!("run {} finished ({})", rid, status),
                    });
                } else if !was_running && running {
                    events.push(Event {
                        kind: KIND_RUN_STARTED.to_string(),
                        level: "info".to_string(),
                        summary: format!("run {} started", rid),
                    });
                }
            }
        }
    }
    events
}

/// Port of `detect_events`. `prev` is `None` on the first tick (baseline; always
/// `[]`), same as the Python.
pub fn detect_events(prev: Option<&Signals>, curr: &Signals) -> Vec<Event> {
    let Some(prev) = prev else {
        return Vec::new();
    };

    let mut events = Vec::new();

    // NEW failures - diffed by episode id, not by count.
    let prev_ids: HashSet<&str> = prev.failures.iter().map(|f| f.id.as_str()).collect();
    let fresh: Vec<&crate::signals::Failure> =
        curr.failures.iter().filter(|f| !prev_ids.contains(f.id.as_str())).collect();
    if !fresh.is_empty() {
        let names: Vec<String> = fresh
            .iter()
            .take(3)
            .map(|f| {
                let text = if !f.decision.is_empty() { &f.decision } else { &f.accion };
                let text = if text.is_empty() { "episode" } else { text.as_str() };
                text.chars().take(60).collect::<String>()
            })
            .collect();
        let extra = if fresh.len() > 3 { format!(" (+{} more)", fresh.len() - 3) } else { String::new() };
        events.push(Event {
            kind: KIND_FAILURES.to_string(),
            level: "alert".to_string(),
            summary: format!("{} new failure(s): {}{}", fresh.len(), names.join("; "), extra),
        });
    }

    // health WORSENED - worst level climbed into warn/alert territory.
    let prev_h = &prev.health.worst_level;
    let curr_h = &curr.health.worst_level;
    if health_order(curr_h) > health_order(prev_h) && (curr_h == "warn" || curr_h == "alert") {
        let detail = if curr.health.failing.is_empty() {
            String::new()
        } else {
            format!(": {}", curr.health.failing.join(", "))
        };
        events.push(Event {
            kind: KIND_HEALTH.to_string(),
            level: curr_h.clone(),
            summary: format!("health={} (was {}){}", curr_h, prev_h, detail),
        });
    }

    // NEW pending approvals - the parked-work queue grew.
    let prev_ap = prev.approvals.len();
    let curr_ap = curr.approvals.len();
    if curr_ap > prev_ap {
        let new = curr_ap - prev_ap;
        events.push(Event {
            kind: KIND_APPROVALS.to_string(),
            level: "warn".to_string(),
            summary: format!("{} new pending approval(s) (total {})", new, curr_ap),
        });
    }

    // run STARTED / FINISHED.
    events.extend(diff_runs(&prev.agent_runs, &curr.agent_runs));

    events
}

/// Port of `_is_important`.
pub fn is_important(event: &Event) -> bool {
    match event.kind.as_str() {
        KIND_FAILURES | KIND_APPROVALS => true,
        KIND_HEALTH => event.level == "alert",
        _ => false,
    }
}

/// Port of `_fingerprint`: a stable, sorted `"kind:summary"` join.
pub fn fingerprint(events: &[Event]) -> String {
    let mut parts: Vec<String> = events.iter().map(|e| format!("{}:{}", e.kind, e.summary)).collect();
    parts.sort();
    parts.join("|")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::signals::{Approval, Failure, Health, Signals};

    fn base_signals() -> Signals {
        Signals {
            instance_id: "inst_test".into(),
            episodes: vec![],
            health: Health { worst_level: "info".into(), ok: true, failing: vec![], alerted: vec![] },
            flows: vec![],
            approvals: vec![],
            failures: vec![],
            agent_runs: Default::default(),
        }
    }

    #[test]
    fn first_tick_never_reports_events() {
        let curr = base_signals();
        assert!(detect_events(None, &curr).is_empty());
    }

    #[test]
    fn identical_snapshots_report_nothing() {
        let s = base_signals();
        assert!(detect_events(Some(&s), &s).is_empty());
    }

    #[test]
    fn new_failure_by_id_is_reported() {
        let prev = base_signals();
        let mut curr = base_signals();
        curr.failures.push(Failure {
            id: "e1".into(),
            department: None,
            decision: "send_digest".into(),
            accion: "send_digest".into(),
            resultado: "failed".into(),
            creado_en: "2026-07-24T10:00:00Z".into(),
        });
        let events = detect_events(Some(&prev), &curr);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, KIND_FAILURES);
        assert!(is_important(&events[0]));
    }

    #[test]
    fn same_failure_id_is_not_reported_twice() {
        let mut prev = base_signals();
        prev.failures.push(Failure {
            id: "e1".into(),
            department: None,
            decision: "d".into(),
            accion: "a".into(),
            resultado: "failed".into(),
            creado_en: "2026-07-24T10:00:00Z".into(),
        });
        let curr = prev.clone();
        assert!(detect_events(Some(&prev), &curr).is_empty());
    }

    #[test]
    fn health_warn_to_alert_is_reported_and_important() {
        let mut prev = base_signals();
        prev.health.worst_level = "warn".into();
        let mut curr = base_signals();
        curr.health.worst_level = "alert".into();
        curr.health.failing = vec!["disk".into()];
        let events = detect_events(Some(&prev), &curr);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, KIND_HEALTH);
        assert!(is_important(&events[0]));
    }

    #[test]
    fn health_info_to_warn_is_reported_but_not_important() {
        let prev = base_signals();
        let mut curr = base_signals();
        curr.health.worst_level = "warn".into();
        let events = detect_events(Some(&prev), &curr);
        assert_eq!(events.len(), 1);
        assert!(!is_important(&events[0]));
    }

    #[test]
    fn new_approval_is_reported_and_important() {
        let prev = base_signals();
        let mut curr = base_signals();
        curr.approvals.push(Approval {
            id: "ap1".into(),
            tipo: "t".into(),
            estado: "pendiente".into(),
            creado_en: "2026-07-24T10:00:00Z".into(),
        });
        let events = detect_events(Some(&prev), &curr);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, KIND_APPROVALS);
        assert!(is_important(&events[0]));
    }

    #[test]
    fn fingerprint_is_order_independent() {
        let a = vec![
            Event { kind: "k1".into(), level: "info".into(), summary: "s1".into() },
            Event { kind: "k2".into(), level: "info".into(), summary: "s2".into() },
        ];
        let b = vec![a[1].clone(), a[0].clone()];
        assert_eq!(fingerprint(&a), fingerprint(&b));
    }

    #[test]
    fn fingerprint_empty_is_empty_string() {
        assert_eq!(fingerprint(&[]), "");
    }
}
