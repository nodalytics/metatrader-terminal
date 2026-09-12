#!/bin/bash

# Only install if not already present
if [ ! -f "/opt/wineprefix/drive_c/Metatrader-5/terminal64.exe" ]; then
    echo "MetaTrader 5 not found. Starting installation..."

    # MetaTrader download url
    URL="https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
    # WebView2 Runtime download url
    URL_WEBVIEW="https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/c1336fd6-a2eb-4669-9b03-949fc70ace0e/MicrosoftEdgeWebview2Setup.exe"

    # Download
    wget -q $URL
    wget -q $URL_WEBVIEW

    # Set environment to Windows 10
    winecfg -v=win11

    # Install WebView2
    wine MicrosoftEdgeWebview2Setup.exe /silent /install
    wineserver -w

    # Install MT5
    wine mt5setup.exe /auto /path:"C:\Metatrader-5"
    wineserver -w

    # Disable LiveUpdate immediately after install (before any launch)
    # to prevent terminal from auto-updating to a build newer than
    # the MetaTrader5 Python library (5.0.5640 on PyPI).
    MT5_CFG_DIR="/opt/wineprefix/drive_c/Metatrader-5/Config"
    mkdir -p "$MT5_CFG_DIR"
    { printf '\xFF\xFE'; printf '[LiveUpdate]\r\nLiveUpdateMode=2\r\n' | iconv -f UTF-8 -t UTF-16LE; } > "$MT5_CFG_DIR/terminal.ini"
    { printf '\xFF\xFE'; printf '[Experts]\r\nEnabled=1\r\n' | iconv -f UTF-8 -t UTF-16LE; } > "$MT5_CFG_DIR/common.ini"
    echo "LiveUpdate disabled and algo trading enabled."

    # Clean up
    rm mt5setup.exe MicrosoftEdgeWebview2Setup.exe
else
    echo "MetaTrader 5 already installed."
fi

# Run MT5 (Skip if in BUILD_MODE)
if [ "$BUILD_MODE" = "1" ]; then
    echo "Metatrader 5 installed successfully (Build Mode). Skipping launch."
    exit 0
fi

# Keep MT5 alive — restart it whenever it exits so the VNC auto-login
# script has time to type credentials into the GUI. On Wine 10.0, MT5
# exits immediately if no server is configured; the auto-login process
# needs the terminal open to enter login details via VNC.
LOGIN_MARKER="/tmp/login_complete"

# Is a terminal already up? MT5 refuses a second instance on the same portable
# data directory and **exits 0 immediately** when it finds one - which is
# indistinguishable, to the loop below, from a clean shutdown.
#
# That cost a day of trading. On 2026-09-11 one launch exited before login, the
# loop relaunched into an instance that was still alive, and every relaunch from
# then on exited 0 at once: **11,945 restarts over eighteen hours**, about
# eleven a minute. The terminal itself was fine the whole time - one process,
# PID 240, serving quotes - but the GUI the VNC auto-login types into was
# replaced every five seconds, so Ctrl+E never landed, AutoTrading stayed off,
# and **171 orders were rejected with nothing filled**.
#
# `pgrep -f` rather than a PID file: a PID file records what this script
# started, and the process that matters may have been started by a previous
# incarnation of it.
terminal_running() {
    pgrep -f 'terminal64.exe' >/dev/null 2>&1
}

while true; do
    if terminal_running; then
        # Do not launch a second one. Wait for the one that exists to go away,
        # which is the only event that should cause a launch.
        sleep 5
        continue
    fi

    echo "Launching MetaTrader 5..."
    wine /opt/wineprefix/drive_c/Metatrader-5/terminal64.exe /portable
    EXIT_CODE=$?

    # A launch that returns in under a few seconds did not run a terminal - it
    # found one. Said out loud, because a silent fast loop is what made the
    # original fault invisible in a log nobody reads at eleven lines a minute.
    if terminal_running; then
        echo "MT5 exited (code $EXIT_CODE) but a terminal is still running — not relaunching."
        sleep 5
        continue
    fi

    # If auto-login has completed and MT5 exits, it's a real crash — still restart
    if [ -f "$LOGIN_MARKER" ]; then
        echo "MT5 exited (code $EXIT_CODE) after login — restarting in 5s..."
        sleep 5
    else
        echo "MT5 exited (code $EXIT_CODE) before login — restarting in 2s..."
        sleep 2
    fi
done