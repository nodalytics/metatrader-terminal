#!/usr/bin/env bash
# Hold a reverse tunnel from this MT5 host to the trading box.
#
# The trading service runs in Docker on a remote instance and reaches this
# terminal at `host.docker.internal:8000`. Nothing on that instance listens on
# 8000 - the port is held open from *this* side, and the container reaches it
# because the forward is bound to the Docker bridge address rather than to
# loopback.
#
#   this host :8000  --ssh-->  remote 172.17.0.1:8000  -->  container
#
# Verified on the live box: the listener on 172.17.0.1:8000 is an `ssh`
# process, not a service. `curl 127.0.0.1:8000` there answers nothing and that
# is correct - binding to loopback would leave the container unable to see it.
#
#   BIND=172.17.0.1 REMOTE=ubuntu@1.2.3.4 KEY=~/.ssh/id_ed25519 ./reverse-tunnel.sh
set -euo pipefail

REMOTE="${REMOTE:?set REMOTE, as user@host}"
KEY="${KEY:-$HOME/.ssh/id_ed25519}"
# The Docker bridge on the remote, which is what `host.docker.internal`
# resolves to from inside a container there. Not loopback: see above.
BIND="${BIND:-172.17.0.1}"
REMOTE_PORT="${REMOTE_PORT:-8000}"
LOCAL_PORT="${LOCAL_PORT:-8000}"

# The remote sshd must allow a forward to a non-loopback address, which is off
# by default. `GatewayPorts clientspecified` is the narrow setting - it permits
# exactly the bind address the client asks for and nothing else. `yes` would
# bind every forward to all interfaces, which on a public instance is an open
# port to a trading terminal.
#
#   /etc/ssh/sshd_config.d/tunnel.conf:  GatewayPorts clientspecified
#
# Without it the forward silently falls back to loopback and the container sees
# nothing, which looks like a broken bridge rather than a misconfigured sshd.

exec ssh -N \
  -i "$KEY" \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 \
  -o ServerAliveCountMax=3 \
  -o TCPKeepAlive=yes \
  -o StrictHostKeyChecking=accept-new \
  # **127.0.0.1, not localhost.** The container publishes 8000 on IPv4
  # loopback only, and ssh resolves this hostname on *this* side: where
  # localhost resolves to ::1 first, every forwarded connection is refused and
  # the tunnel comes up **bound and dead**. That failure passes every check
  # short of an actual request - `systemctl is-active` says active, the
  # listener appears on the right address on the remote, and the -R string in
  # `ps` looks correct - while nothing flows. Measured 2026-10-27: the desk sat
  # at "cannot attach" for 47 attempts against a listening socket.
  -R "${BIND}:${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT}" \
  "$REMOTE"
