#!/bin/bash
# Wrapper: run VNC auto-login under Wine, confirm AutoTrading against the
# terminal, then create the marker file for the API server. No wineserver kill
# needed — the connector passes credentials to mt5.initialize() directly.
#
# The marker is this script's contract with `run-server.sh`, which blocks on it
# before starting the API. So everything asserted here is asserted *before the
# API serves its first request*, and the marker means "a desk that can act",
# not merely "a desk that logged in".

# Overridable so the tests can drive this script against stubs. The defaults
# are what the container runs on.
LOG=${AUTO_LOGIN_LOG:-/tmp/auto_login.log}
ALGO_LOG=${TOGGLE_ALGO_LOG:-/tmp/toggle_algo.log}
LOGIN_MARKER=${LOGIN_MARKER:-/tmp/login_complete}
ASSETS=${ASSETS_DIR:-/root}
VNC_WAIT=${VNC_WAIT:-30}
ALGO_ROUNDS=${ALGO_ROUNDS:-3}
ALGO_WAIT=${ALGO_WAIT:-60}
REQUIRE_ALGO=${MT5_REQUIRE_ALGO:-1}

# AutoTrading does not survive a recreate on its own.
#
# `auto_login.py` presses Ctrl+E as part of typing credentials into a fresh
# GUI, but that press lands on a window seconds old, and on 2026-09-12 the
# terminal's restart loop meant it never landed on a window that lived long
# enough to act on it. `common.ini` is no evidence either way: `Enabled=1`
# records what the terminal was *asked* for, and MT5 rewrites the file when it
# exits, so a terminal can boot with the ini set and the toggle off.
#
# The only thing worth believing is the terminal. `toggle_algo.py` reads
# `terminal_info().trade_allowed` — the value an order is actually answered
# with — presses only on a genuine mismatch, and re-reads to confirm. So this
# is an assert, not a best effort, and a failure here must not reach the API.
#
# It is watched through its log rather than waited on because `wine python`
# does not reliably exit when the script does: the wineserver keeps the process
# alive, which is the same reason the auto-login below is watched this way. The
# verdict is therefore taken from what the script *said*, never from `$?` — a
# hang would otherwise read as a pass — and the process is killed once it has
# said it.
confirm_autotrading() {
    local round i pid
    for round in $(seq 1 "$ALGO_ROUNDS"); do
        : > "$ALGO_LOG"
        wine python "$ASSETS/toggle_algo.py" >> "$ALGO_LOG" 2>&1 &
        pid=$!
        for i in $(seq 1 "$ALGO_WAIT"); do
            if grep -q "AutoTrading is on" "$ALGO_LOG" 2>/dev/null; then
                kill $pid 2>/dev/null
                return 0
            fi
            # Both of the script's own verdicts of failure. Breaking on them
            # keeps a decided failure from waiting out the whole timeout.
            if grep -qE "could not turn AutoTrading on|cannot reach MT5" \
                "$ALGO_LOG" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        kill $pid 2>/dev/null
        echo "AutoTrading not confirmed (round ${round}/${ALGO_ROUNDS}):"
        cat "$ALGO_LOG" 2>/dev/null
        [ "$round" -lt "$ALGO_ROUNDS" ] && sleep 5
    done
    return 1
}

# Wait for VNC to be ready
echo "Waiting for VNC server..."
for i in $(seq 1 "$VNC_WAIT"); do
    if bash -c "echo > /dev/tcp/localhost/5900" 2>/dev/null; then
        echo "VNC ready after ${i}s"
        break
    fi
    sleep 1
done

# Run auto-login in background, capture output.
# wine python hangs after script completes (wineserver keeps it alive),
# so we watch the log for the completion message.
wine python "$ASSETS/auto_login.py" > "$LOG" 2>&1 &
WINE_PID=$!

# Wait for completion message or timeout (180s)
# Login takes ~20s + 30s LiveUpdate wait = ~50s minimum
echo "Waiting for auto-login to complete..."
for i in $(seq 1 90); do
    if grep -q "Auto-login sequence completed" "$LOG" 2>/dev/null; then
        echo "Auto-login succeeded."
        if confirm_autotrading; then
            echo "AutoTrading confirmed against the terminal: trade_allowed is true."
        elif [ "$REQUIRE_ALGO" = "0" ]; then
            echo "AutoTrading could NOT be confirmed, and MT5_REQUIRE_ALGO=0, so the"
            echo "API will start and serve reads. Every order will be refused (10027)."
        else
            echo "AutoTrading could NOT be confirmed, so the marker is not being"
            echo "created and the API will not start. A desk that decides and cannot"
            echo "act is worse than one that is plainly down: its only symptom is a"
            echo "log line nobody is watching, and it costs a day before anyone asks."
            echo "Set MT5_REQUIRE_ALGO=0 to serve reads from a terminal that cannot trade."
            exit 1
        fi
        echo "1" > "$LOGIN_MARKER"
        echo "Marker created."
        exit 0
    fi
    if grep -q "An error occurred" "$LOG" 2>/dev/null; then
        echo "Auto-login failed:"
        cat "$LOG"
        exit 1
    fi
    sleep 2
done

echo "Auto-login timed out after 180s"
cat "$LOG"
exit 1
