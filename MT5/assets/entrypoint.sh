#!/bin/bash

# 0. Clean up stale state from previous runs (e.g. docker restart)
#    - login marker: forces auto-login to re-run and dismiss LiveUpdate popup
#    - X11 lock/socket: prevents VNC from failing with "display already in use"
rm -f /tmp/login_complete /tmp/.X0-lock
rm -f /tmp/.X11-unix/X0 2>/dev/null

# Settle the MT5 config before the terminal starts.
#
# Two things, and both have to happen here rather than afterwards: the terminal
# reads this file at startup and rewrites it from memory on exit, so anything
# changed while it runs is discarded.
#
#   * **Algo trading.** The terminal disables it on an account change and
#     persists `Enabled=0`, which silently stops every order.
#   * **Max bars in chart.** This caps how much history the terminal keeps per
#     chart, and therefore how much `copy_rates_*` can return. At the installed
#     default of 100,000 the hourly series for the FX majors stopped at exactly
#     99,999 bars - August 2010 - while gold, which has less history than that,
#     stopped at its true start. The cap was the binding constraint, not the
#     broker.
#
# `common.ini` is UTF-16, which is why this is a Python block and not `sed`. A
# byte-level replacement was enough for the algo flag; setting a numeric value
# whose digit count changes is not, so the file is decoded properly.
MT5_COMMON="${WINEPREFIX:-/opt/wineprefix}/drive_c/Metatrader-5/Config/common.ini"
MT5_MAX_BARS="${MT5_MAX_BARS:-1000000}"
if [ -f "$MT5_COMMON" ]; then
    MT5_COMMON="$MT5_COMMON" MT5_MAX_BARS="$MT5_MAX_BARS" python3 - <<'SETTLE'
import os
import re

path = os.environ["MT5_COMMON"]
want = os.environ.get("MT5_MAX_BARS", "1000000").strip()

raw = open(path, "rb").read()
try:
    text = raw.decode("utf-16")
except UnicodeDecodeError:
    # Not the encoding we expect. Leaving it alone beats corrupting the only
    # copy of the terminal's settings.
    print("==> common.ini is not UTF-16; left untouched")
    raise SystemExit(0)

before = text

# Algo trading, exactly as before: the first `Enabled=0` in the file.
text = text.replace("Enabled=0", "Enabled=1", 1)

if want.isdigit() and int(want) > 0:
    found = re.search(r"^MaxBars=(\d+)\s*$", text, re.MULTILINE)
    if found:
        if found.group(1) != want:
            text = text[: found.start()] + f"MaxBars={want}" + text[found.end() :]
            print(f"==> MaxBars {found.group(1)} -> {want}")
    elif "[Charts]" in text:
        text = text.replace("[Charts]", f"[Charts]\r\nMaxBars={want}", 1)
        print(f"==> MaxBars set to {want}")
    else:
        # **A freshly built image has no `[Charts]` section at all.** The terminal
        # writes one the first time it runs, which is after this script, so on the
        # very first boot of a new container there is nothing here to patch and an
        # earlier version of this silently did nothing - the cap stayed at 100,000
        # and the only clue was the absence of a log line. Append the section so
        # the first boot is settled too.
        if not text.endswith(("\n", "\r")):
            text += "\r\n"
        text += f"[Charts]\r\nMaxBars={want}\r\n"
        print(f"==> no [Charts] section; appended one with MaxBars={want}")
else:
    print(f"==> MT5_MAX_BARS={want!r} is not a positive integer; ignored")

if text != before:
    # utf-16 writes the byte-order mark the terminal expects.
    open(path, "wb").write(text.encode("utf-16"))
    if "Enabled=1" in text and "Enabled=0" in before:
        print("==> Algo trading re-enabled in common.ini")
else:
    print("==> common.ini already settled")
SETTLE
fi

# 1. Initialize Authentication (Must happen BEFORE Nginx starts)
if [ -f /root/vnc-auth.sh ]; then
    chmod +x /root/vnc-auth.sh
    /root/vnc-auth.sh
else
    echo "==> WARNING: /root/vnc-auth.sh not found. skipping auth initialization."
fi

# 2. Start supervisord using exec to ensure it is PID 1
echo "==> Starting services via supervisor..."
exec /usr/bin/supervisord -c /etc/supervisord.conf
