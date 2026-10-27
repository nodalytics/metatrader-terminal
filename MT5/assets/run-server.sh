#!/bin/sh

# Wait for auto-login to complete, which now means more than it used to.
#
# `auto_login_wrapper.sh` does not create this marker until it has confirmed
# `terminal_info().trade_allowed` against the running terminal, so waiting on
# it is what keeps the API from serving a desk that can decide and cannot act.
# Before 2026-10-27 the marker meant only that credentials had been typed in,
# and AutoTrading was left to a Ctrl+E that a recreate could lose — so the
# service came up healthy, answered every read, and had every order refused
# with 10027.
#
# The wrapper exits 1 instead of creating the marker when it cannot confirm,
# and its supervisord program does not restart, so this loop waits forever by
# design: the container is then plainly down rather than quietly useless. Set
# MT5_REQUIRE_ALGO=0 to have the wrapper create the marker anyway and serve
# reads from a terminal that cannot trade.
LOGIN_MARKER=${LOGIN_MARKER:-/tmp/login_complete}

echo "Waiting for auto-login to complete..."
while [ ! -f "$LOGIN_MARKER" ]; do
    sleep 2
done
echo "Auto-login marker found: AutoTrading was confirmed before this point."

# Start the server
echo "Starting FastAPI Server..."
cd $HOME/api
wine python -m app
