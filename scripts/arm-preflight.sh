#!/usr/bin/env bash
# Can this host run the amd64 MT5 image?
#
# MetaTrader 5 is an x86-64 Windows binary and Wine has no arm64 build that
# runs one, so there is no native ARM image to build — on ARM the amd64 image
# runs under QEMU. That needs binfmt_misc registered for x86-64, and when it is
# not, the failure is `exec format error` from a container that starts and
# immediately dies, which names neither the cause nor the fix.
#
# This says so before you spend twenty minutes on it.
set -euo pipefail

arch="$(uname -m)"
echo "host architecture: ${arch}"

case "${arch}" in
  x86_64|amd64)
    echo "✅ native amd64 — nothing to do."
    exit 0
    ;;
  aarch64|arm64) ;;
  *)
    echo "⚠️  unrecognised architecture; the image is amd64 and may not run."
    exit 1
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ docker is not installed."
  exit 1
fi

# Docker Desktop on Apple Silicon ships Rosetta or QEMU and needs no setup;
# Linux hosts usually do. Asking docker directly is better than guessing which.
echo "checking whether docker can run an amd64 image..."
if docker run --rm --platform linux/amd64 alpine:3 uname -m 2>/dev/null | grep -q x86_64; then
  echo "✅ amd64 emulation works — MT5 will run, more slowly than on amd64."
  echo "   Expect the terminal's GUI to be sluggish over VNC; the API is fine."
  exit 0
fi

echo "❌ this host cannot run amd64 images yet."
echo
echo "On Linux, register the emulators once per boot:"
echo "    docker run --privileged --rm tonistiigi/binfmt --install amd64"
echo
echo "On Docker Desktop (macOS), enable Rosetta or QEMU emulation in"
echo "Settings → General → 'Use Rosetta for x86_64/amd64 emulation'."
echo
echo "Then run this script again."
exit 1
