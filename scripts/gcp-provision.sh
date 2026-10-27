#!/usr/bin/env bash
# Provision a GCP VM that runs this terminal, and can double as the research lab.
#
#   PROJECT=trim-odyssey-512319-g2 ./scripts/gcp-provision.sh
#   PROJECT=... MACHINE=e2-standard-8 DISK=400GB ./scripts/gcp-provision.sh
#
# It creates the instance and installs Docker. It does **not** start the
# terminal or the tunnel: both need secrets (MT5 login, an SSH key the trading
# box trusts) and those are placed by hand, printed as next steps at the end.
#
# ## Why these choices
#
# **x86, not ARM.** `MT5/docker-compose.yml` pins `platform: linux/amd64`
# because MetaTrader 5 is an x86-64 Windows binary and Wine has no arm64 build
# that runs one. On ARM it works under QEMU and is slower, so the cheap GCP
# ARM types (t2a) are the wrong saving: the thing this host exists to run is
# the thing they emulate.
#
# **asia-south1, not the configured default.** The trading box is in AWS
# ap-south-1 (Mumbai) and reaches the terminal over the reverse tunnel on every
# decision, so the round trip is in the decision path. GCP's Mumbai region puts
# it next door — single-digit milliseconds instead of the ~200ms a US region
# would add to every call.
#
# **4 vCPU / 16GB is mostly for the lab.** The terminal is capped at `cpus:
# '0.8'` and `memory: 4G` by its own compose - 4G rather than 1G because a
# 100,000-bar history request died inside the terminal at 1G. So the terminal
# wants about one core, and the rest is what makes this box useful as the lab
# as well. Resize later with stop / `set-machine-type` / start; no rebuild.
#
# **No service account and no scopes.** This box calls no Google API: it pulls
# its image from ghcr.io, runs the terminal and the API, holds the reverse
# tunnel, and reads local data. An earlier version of this script granted the
# default service account `cloud-platform`, which is every API in the project -
# and this is the host holding MT5_PASSWORD and the key the desk authenticates
# with, so that turned "someone got into a Wine-hosted Windows terminal" into
# "someone has the GCP project". `gcloud compute ssh` is unaffected: it
# authenticates as you, not as the instance. Add `--service-account` with
# `logging-write,monitoring-write` only if Cloud Logging is ever actually
# wanted - the ops path here is SSH and docker logs, as on the trading box.
#
# **Nothing is opened to the internet.** The compose binds 6901 and 8000 to
# 127.0.0.1 and both are reached over SSH, so this creates no ingress rule for
# them. That is deliberate: Docker writes its own iptables rules ahead of ufw,
# so a published port cannot be closed again with `ufw deny` - the defence has
# to be not publishing it.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT, e.g. PROJECT=trim-odyssey-512319-g2}"
ZONE="${ZONE:-asia-south1-a}"
NAME="${NAME:-mt5-lab}"
MACHINE="${MACHINE:-e2-standard-4}"
DISK="${DISK:-200GB}"
DISK_TYPE="${DISK_TYPE:-pd-balanced}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2204-lts}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"
# ADDRESS=none creates the instance with no external IP. The organisation sets
# `compute.vmExternalIpAccess` to `allValues: DENY`, and a project-level
# allowlist does not override it - adding the instance to the project list was
# accepted and the create was still refused. So `none` is the path that works
# without an organisation-level policy change, and it then needs egress:
# Cloud NAT for apt, the ghcr.io pull and MT5's own connection to the broker.
ADDRESS="${ADDRESS:-auto}"

case "$MACHINE" in
  t2a-*|*-arm*) echo "refusing $MACHINE: ARM runs the terminal under QEMU. See the header." >&2; exit 1 ;;
esac

if gcloud compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  echo "$NAME already exists in $ZONE - nothing created."
  gcloud compute instances describe "$NAME" --zone "$ZONE" --project "$PROJECT" \
    --format="value(name,machineType.basename(),status)"
  exit 0
fi

# Docker from the official repository rather than the distro package: the
# compose file uses `deploy.resources.limits`, which needs the compose v2
# plugin, and Ubuntu's docker.io ships only the v1 script.
read -r -d '' STARTUP <<'SH' || true
#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
# The terminal image is **10.3GB** - it bakes the Kasm desktop and a 2.3GB Wine
# prefix with a terminal64.exe already in it - so pull now, or the first `up`
# is also a 10GB download. Measured on the first provision; an earlier version
# of this comment guessed 3GB.
docker pull --platform linux/amd64 ghcr.io/nodalytics/mt5-terminal:latest || true
touch /var/lib/mt5-provisioned
SH

echo "creating $NAME ($MACHINE, $DISK $DISK_TYPE) in $ZONE ..."
gcloud compute instances create "$NAME" \
  --project "$PROJECT" --zone "$ZONE" \
  --machine-type "$MACHINE" \
  --image-family "$IMAGE_FAMILY" --image-project "$IMAGE_PROJECT" \
  --boot-disk-size "$DISK" --boot-disk-type "$DISK_TYPE" \
  --metadata-from-file startup-script=<(printf '%s' "$STARTUP") \
  --no-service-account --no-scopes \
  $( [ "$ADDRESS" = "none" ] && echo "--no-address" ) \
  --labels "role=mt5-lab,managed-by=metatrader-terminal"

cat <<NOTE

Created. Docker installs in the background; /var/lib/mt5-provisioned appears
when it is done.

  gcloud compute ssh $NAME --zone $ZONE --project $PROJECT

Then, in order:

 1. Write MT5/.env on the host - MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    API_KEY_SEED, VNC_PASSWORD. The desk sends API_KEY_SEED's key as the
    X-API-Key header, so it has to match TRADING_MT5_API_KEY on the trading box.

 2. docker compose -f MT5/docker-compose.yml up -d
    Check it with: curl -s localhost:8000/ (loopback only, by design)

 3. Make a key for the tunnel and add the public half to the trading box:
      ssh-keygen -t ed25519 -N '' -f ~/.ssh/mt5-tunnel
      cat ~/.ssh/mt5-tunnel.pub      # -> trading box ~/.ssh/authorized_keys

 4. The trading box's sshd needs GatewayPorts clientspecified, or the forward
    silently falls back to loopback and the container never sees it. It is
    already set on the current box, since the live tunnel binds 172.17.0.1.

 5. sudo REMOTE=ubuntu@<trading-box> KEY=/root/.ssh/mt5-tunnel \\
      ./scripts/install-tunnel.sh

 6. Seed the Wine prefix from the running terminal BEFORE trusting this one.
    A fresh volume comes up on the image's build, not whatever the live
    terminal updated itself to - and build 6204 is on record breaking the
    Python binding while 6140 was fine. See the volume comment in
    MT5/docker-compose.yml.
NOTE
