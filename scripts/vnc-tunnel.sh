#!/usr/bin/env bash
# Put the terminal's screen on this machine, over SSH.
#
# The MT5 GUI is the authority on a few things the API cannot reach - the
# AutoTrading toggle is the one that matters, and on 2026-09-12 it cost nine
# hours of a live desk sending orders into a terminal that refused all of them.
# `toggle_algo.py` handles that case headlessly now, but when something is wrong
# in a way nobody has automated yet, the fastest answer is to look at the screen.
#
#   ./scripts/vnc-tunnel.sh                        # uses $MT5_HOST, opens a browser
#   ./scripts/vnc-tunnel.sh user@host              # explicit host
#   ./scripts/vnc-tunnel.sh user@host -k ~/.ssh/id -p 6999   # key, local port
#   ./scripts/vnc-tunnel.sh user@host --no-viewer  # tunnel only, connect yourself
#   ./scripts/vnc-tunnel.sh user@host --raw        # native viewer, see below
#
# **The container publishes 6901, not 5900.** `Xtigervnc` listens on 5900
# *inside* the container and `nginx` serves noVNC on 6901, which is what the
# compose file exposes - so the route that works without touching the deployment
# is the web one, and the "GUI" is a browser tab. `--raw` forwards 5900 for a
# native viewer instead, and only works where that port has been published.
#
# **Forwarded, not exposed.** The tunnel binds to loopback on this machine, so
# the terminal's screen is reachable from here and from nowhere else. VNC's own
# authentication is weak by modern standards and the container's password is a
# convenience, not a control - the reason this is safe is that the port never
# leaves the machine you are sitting at.
set -euo pipefail

# Only a bare word is the host. Taking `$1` unconditionally makes `--help` the
# hostname, which is a confusing way to fail at something that was meant to help.
REMOTE="${MT5_HOST:-}"
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
    REMOTE="$1"
    shift
fi

KEY=""
LOCAL_PORT=""
REMOTE_PORT=""
VIEWER=1
RAW=0

while [ $# -gt 0 ]; do
    case "$1" in
        -k|--key)      KEY="$2"; shift 2 ;;
        -p|--port)     LOCAL_PORT="$2"; shift 2 ;;
        -r|--remote-port) REMOTE_PORT="$2"; shift 2 ;;
        --no-viewer)   VIEWER=0; shift ;;
        --raw)         RAW=1; shift ;;
        -h|--help)     sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)             echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$REMOTE" ]; then
    echo "usage: $0 user@host [-k key] [-p local-port] [--raw] [--no-viewer]" >&2
    echo "   or: MT5_HOST=user@host $0" >&2
    exit 2
fi

# noVNC over the published web port by default; raw RFB only when asked for.
if [ "$RAW" = "1" ]; then
    : "${REMOTE_PORT:=5900}"; : "${LOCAL_PORT:=5900}"
else
    : "${REMOTE_PORT:=6901}"; : "${LOCAL_PORT:=6901}"
fi

SSH=(ssh -N)
[ -n "$KEY" ] && SSH+=(-i "$KEY")
# `ExitOnForwardFailure` so a port already in use fails here rather than leaving
# a tunnel that looks up and forwards nothing - the same class of silent success
# the reverse tunnel's `GatewayPorts` note warns about.
SSH+=(-o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" "$REMOTE")

echo "forwarding ${REMOTE} :${REMOTE_PORT} -> 127.0.0.1:${LOCAL_PORT}"
"${SSH[@]}" &
TUNNEL=$!
trap 'kill "$TUNNEL" 2>/dev/null || true' EXIT INT TERM

# Wait for the forward rather than sleeping at it: a fixed sleep is a guess that
# the tunnel is up, and a viewer started too early fails with "connection
# refused" on a tunnel that was about to work.
for _ in $(seq 1 40); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${LOCAL_PORT}") 2>/dev/null; then
        exec 3>&- 3<&-
        break
    fi
    sleep 0.25
done

if ! (exec 3<>"/dev/tcp/127.0.0.1/${LOCAL_PORT}") 2>/dev/null; then
    echo "the forward did not come up on 127.0.0.1:${LOCAL_PORT}" >&2
    exit 1
fi
exec 3>&- 3<&- 2>/dev/null || true

WHERE="http://127.0.0.1:${LOCAL_PORT}/vnc.html?autoconnect=1&resize=remote"
[ "$RAW" = "1" ] && WHERE="127.0.0.1:${LOCAL_PORT}"

if [ "$VIEWER" = "0" ]; then
    echo "tunnel is up: ${WHERE}"
    echo "ctrl-c to stop."
    wait "$TUNNEL"
    exit 0
fi

if [ "$RAW" = "1" ]; then
    for viewer in vncviewer xtightvncviewer remmina vinagre gvncviewer; do
        command -v "$viewer" >/dev/null 2>&1 || continue
        echo "opening ${viewer}"
        case "$viewer" in
            remmina) "$viewer" -c "vnc://${WHERE}" ;;
            vinagre) "$viewer" "vnc://${WHERE}" ;;
            *)       "$viewer" "${WHERE}" ;;
        esac
        exit 0
    done
    echo "no VNC viewer found - the tunnel is up on ${WHERE}."
    echo "install one (apt install tigervnc-viewer) or connect your own; ctrl-c to stop."
    wait "$TUNNEL"
    exit 0
fi

# The browser is backgrounded and the tunnel is waited on, not the other way
# round: closing the tab should not kill the forward, and ctrl-c here should.
for opener in xdg-open open sensible-browser firefox chromium google-chrome; do
    if command -v "$opener" >/dev/null 2>&1; then
        echo "opening ${WHERE}"
        "$opener" "$WHERE" >/dev/null 2>&1 &
        break
    fi
done

echo "tunnel is up: ${WHERE}"
echo "ctrl-c to stop."
wait "$TUNNEL"
