#!/usr/bin/env bash
# Install the reverse tunnel as a service on this MT5 host.
#
#   sudo REMOTE=ubuntu@1.2.3.4 KEY=/home/mt5/.ssh/id_ed25519 ./install-tunnel.sh
set -euo pipefail

REMOTE="${REMOTE:?set REMOTE, as user@host}"
KEY="${KEY:-$HOME/.ssh/id_ed25519}"
BIND="${BIND:-172.17.0.1}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 0755 "$here/reverse-tunnel.sh" /usr/local/bin/mt5-reverse-tunnel.sh
install -m 0644 "$here/mt5-tunnel.service" /etc/systemd/system/mt5-tunnel.service

# 0600: it names a key path and a host, and the unit reads it as root.
umask 077
cat > /etc/mt5-tunnel.env <<ENV
REMOTE=$REMOTE
KEY=$KEY
BIND=$BIND
REMOTE_PORT=${REMOTE_PORT:-8000}
LOCAL_PORT=${LOCAL_PORT:-8000}
ENV

systemctl daemon-reload
systemctl enable --now mt5-tunnel.service
systemctl --no-pager status mt5-tunnel.service | head -12

cat <<'NOTE'

Check it from the remote, not from here. A tunnel that is up locally and bound
to the wrong address looks identical to a working one from this side:

    ss -tlnp | grep 8000        # the listener should be `ssh`, on the bridge
    curl -s -o /dev/null -w '%{http_code}\n' http://172.17.0.1:8000/health

If that binds to 127.0.0.1 instead, sshd is refusing the non-loopback forward -
add `GatewayPorts clientspecified` to /etc/ssh/sshd_config.d/tunnel.conf.
NOTE
