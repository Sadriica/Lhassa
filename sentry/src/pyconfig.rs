// Mirrors the handful of lhassa.config.Config fields the monitor watcher reads.
// Same env var names, same defaults, so a sentry run under the same environment
// sees the same knobs as the Python watcher.

use std::env;
use std::path::PathBuf;

fn env_str(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_string())
}

fn env_f64(name: &str, default: f64) -> f64 {
    env::var(name)
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(default)
}

fn home_dir() -> PathBuf {
    env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("."))
}

#[derive(Debug, Clone)]
pub struct SentryConfig {
    pub instance_id: String,
    pub brain_db_path: PathBuf,
    pub health_dir: PathBuf,
    pub flows_file: PathBuf,
    pub monitor_failure_window_h: f64,
    pub monitor_department: String,
    pub monitor_idle_interval: f64,
    pub monitor_active_interval: f64,
}

impl SentryConfig {
    pub fn from_env() -> Self {
        let home = home_dir();
        SentryConfig {
            instance_id: env_str("LHASSA_INSTANCE_ID", "inst_personal"),
            brain_db_path: PathBuf::from(env_str(
                "LHASSA_BRAIN_DB_PATH",
                home.join(".lhassa").join("brain.db").to_string_lossy().as_ref(),
            )),
            health_dir: PathBuf::from(env_str(
                "LHASSA_HEALTH_DIR",
                home.join(".lhassa").join("health").to_string_lossy().as_ref(),
            )),
            flows_file: PathBuf::from(env_str(
                "LHASSA_FLOWS_FILE",
                home.join(".lhassa").join("flows.json").to_string_lossy().as_ref(),
            )),
            monitor_failure_window_h: env_f64("LHASSA_MONITOR_FAILURE_WINDOW_H", 24.0),
            monitor_department: env_str("LHASSA_MONITOR_DEPARTMENT", "interns"),
            monitor_idle_interval: env_f64("LHASSA_MONITOR_IDLE_INTERVAL", 300.0),
            monitor_active_interval: env_f64("LHASSA_MONITOR_ACTIVE_INTERVAL", 15.0),
        }
    }

    #[cfg(test)]
    pub fn for_test(brain_db_path: PathBuf, health_dir: PathBuf, flows_file: PathBuf) -> Self {
        SentryConfig {
            instance_id: "inst_test".to_string(),
            brain_db_path,
            health_dir,
            flows_file,
            monitor_failure_window_h: 24.0,
            monitor_department: "interns".to_string(),
            monitor_idle_interval: 300.0,
            monitor_active_interval: 15.0,
        }
    }
}
