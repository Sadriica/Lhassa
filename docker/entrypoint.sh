#!/usr/bin/env bash
# Lhassa container entrypoint.
#
# Selects what this container runs via the LHASSA_RUN env var so the same image
# can back several compose services:
#
#   LHASSA_RUN=api        (default) — dashboard + REST API + scheduler (no voice)
#   LHASSA_RUN=heartbeat            — run one brain beat, then loop every
#                                     LHASSA_HEARTBEAT_INTERVAL seconds (default 900).
#                                     Set the interval to 0 to run a single beat and exit.
#   LHASSA_RUN=telegram             — the Telegram inbound long-poller
#   LHASSA_RUN=monitor               — the always-on $0 monitor watcher (org.watcher);
#                                     gated by LHASSA_MONITOR_ENABLED (logs once + exits
#                                     cleanly, exit code 0, when off — same
#                                     credential/opt-in-gated discipline as telegram).
#
# Any explicit command/args passed to the container are executed verbatim (so
# `docker run lhassa:dev python -c "..."` still works for debugging/sanity checks).
set -euo pipefail

# An explicit command overrides the mode dispatch below.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

mode="${LHASSA_RUN:-api}"

case "$mode" in
    api)
        echo "lhassa: starting dashboard/API + scheduler on ${LHASSA_API_HOST:-0.0.0.0}:${LHASSA_API_PORT:-7766}"
        exec python /app/docker/run_api.py
        ;;

    heartbeat)
        interval="${LHASSA_HEARTBEAT_INTERVAL:-900}"
        # A non-positive interval means "one beat, then exit" (host cron/systemd
        # can own the schedule instead — see doc/deployment.md).
        if ! [[ "$interval" =~ ^[0-9]+$ ]] || [ "$interval" -le 0 ]; then
            echo "lhassa: single heartbeat beat"
            exec /app/scripts/lhassa-heartbeat
        fi
        echo "lhassa: resident heartbeat loop every ${interval}s"
        # Resident mode: one interpreter/import, then beat + sleep forever
        # in-process (lhassa.core.heartbeat_loop), instead of relaunching the
        # whole Python process every tick. SIGTERM/SIGINT (docker stop) are
        # handled cleanly by the loop itself.
        exec /app/scripts/lhassa-heartbeat --loop "$interval"
        ;;

    telegram)
        echo "lhassa: starting Telegram inbound poller"
        exec python -m lhassa.telegram.poller
        ;;

    monitor)
        echo "lhassa: starting monitor watcher (LHASSA_MONITOR_ENABLED=${LHASSA_MONITOR_ENABLED:-0})"
        exec python -m lhassa.org.watcher
        ;;

    *)
        echo "lhassa: unknown LHASSA_RUN='${mode}' (expected api | heartbeat | telegram | monitor)" >&2
        exit 64
        ;;
esac
