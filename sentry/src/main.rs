// lhassa-sentry: a shadow-mode Rust port of the monitor watcher's deterministic
// ear (lhassa/org/watcher.py + lhassa/org/monitor.py::gather_signals). v1 is
// READ-ONLY and observe-only: it never writes to the brain DB, never mints
// anything, never talks to the network. Its only job is to prove it can see the
// same events the Python watcher sees, at a fraction of the resident memory.

mod events;
mod pyconfig;
mod signals;

use std::io::Write;
use std::time::Duration;

use serde::Serialize;

use events::Event;
use pyconfig::SentryConfig;
use signals::Signals;

#[derive(Serialize)]
struct Tick<'a> {
    tick: u64,
    ts: f64,
    instance_id: &'a str,
    department: &'a str,
    // Cadence config the Python watcher would use for adaptive sleep (idle vs.
    // active); the sentry's own loop cadence is fixed via --loop in v1, this is
    // reported purely so a shadow comparison can see the two are reading the same
    // config, not because the sentry adapts its cadence yet.
    monitor_idle_interval: f64,
    monitor_active_interval: f64,
    signals: &'a Signals,
    events: &'a [Event],
    important: Vec<&'a Event>,
    fingerprint: String,
}

fn now_secs() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

struct Args {
    once: bool,
    loop_interval: Option<f64>,
    json: bool,
    json_lines: bool,
}

fn parse_args() -> Result<Args, String> {
    let mut once = false;
    let mut loop_interval: Option<f64> = None;
    let mut json = false;
    let mut json_lines = false;

    let mut argv = std::env::args().skip(1).peekable();
    while let Some(arg) = argv.next() {
        match arg.as_str() {
            "--once" => once = true,
            "--loop" => {
                let val = argv
                    .next()
                    .ok_or_else(|| "--loop requires a seconds argument".to_string())?;
                let secs: f64 = val
                    .parse()
                    .map_err(|_| format!("--loop: invalid seconds {:?}", val))?;
                loop_interval = Some(secs);
            }
            "--json" => json = true,
            "--json-lines" => json_lines = true,
            "-h" | "--help" => {
                print_help();
                std::process::exit(0);
            }
            other => return Err(format!("unrecognized argument: {}", other)),
        }
    }

    if once && loop_interval.is_some() {
        return Err("--once and --loop are mutually exclusive".to_string());
    }
    if !once && loop_interval.is_none() {
        return Err("specify either --once or --loop <seconds>".to_string());
    }
    Ok(Args { once, loop_interval, json, json_lines })
}

fn print_help() {
    println!(
        "lhassa-sentry - read-only shadow ear for the Lhassa monitor watcher\n\n\
         USAGE:\n\
         \x20 lhassa-sentry --once --json\n\
         \x20 lhassa-sentry --loop <seconds> --json-lines\n\n\
         v1 never writes to the brain DB and never launches anything. It only\n\
         observes and reports, for shadow-mode comparison against the Python\n\
         watcher (scripts/sentry-shadow.py)."
    );
}

fn one_tick(config: &SentryConfig, prev: Option<&Signals>, tick_no: u64) -> (Signals, Vec<Event>) {
    let curr = signals::gather_signals(config);
    let evs = events::detect_events(prev, &curr);
    let _ = tick_no; // reserved for future use (e.g. periodic full re-baseline)
    (curr, evs)
}

fn emit(tick_no: u64, config: &SentryConfig, curr: &Signals, evs: &[Event]) {
    let important: Vec<&Event> = evs.iter().filter(|e| events::is_important(e)).collect();
    let fp = events::fingerprint(evs);
    let tick = Tick {
        tick: tick_no,
        ts: now_secs(),
        instance_id: &config.instance_id,
        department: &config.monitor_department,
        monitor_idle_interval: config.monitor_idle_interval,
        monitor_active_interval: config.monitor_active_interval,
        signals: curr,
        events: evs,
        important,
        fingerprint: fp,
    };
    let out = serde_json::to_string(&tick).expect("Tick always serializes");
    println!("{}", out);
    let _ = std::io::stdout().flush();
}

fn main() {
    let args = match parse_args() {
        Ok(a) => a,
        Err(msg) => {
            eprintln!("lhassa-sentry: {}", msg);
            print_help();
            std::process::exit(2);
        }
    };

    let config = SentryConfig::from_env();

    if args.once {
        let (curr, evs) = one_tick(&config, None, 0);
        if args.json || args.json_lines {
            emit(0, &config, &curr, &evs);
        } else {
            eprintln!("{} event(s) detected", evs.len());
        }
        return;
    }

    // --loop mode: resident, one JSON line per tick, never sleeps between reads
    // of anything but the clock. Baseline on tick 0 (no prior snapshot, so no
    // events, matching the Python's "first tick never reports" rule), then diff
    // against the previous tick's snapshot from then on.
    let interval = Duration::from_secs_f64(args.loop_interval.unwrap_or(300.0).max(0.0));
    let mut prev: Option<Signals> = None;
    let mut tick_no: u64 = 0;
    loop {
        let (curr, evs) = one_tick(&config, prev.as_ref(), tick_no);
        emit(tick_no, &config, &curr, &evs);
        prev = Some(curr);
        tick_no += 1;
        std::thread::sleep(interval);
    }
}
