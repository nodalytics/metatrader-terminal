"""`auto_login_wrapper.sh`: the marker it writes is a promise that the desk can act.

`run-server.sh` blocks on `/tmp/login_complete` before starting the API, so that marker is
the last gate before the service answers anything. Until 2026-10-27 it meant only that
credentials had been typed into the GUI, and AutoTrading was left to a Ctrl+E inside
`auto_login.py` that a recreate could lose - which is how the desk came up healthy,
answered every read, and had every order refused with 10027.

**The test that matters most is `test_no_marker_when_autotrading_cannot_be_turned_on`.**
The rest is boundary work. That one is the change: failing to confirm `trade_allowed` has
to stop the API from serving, because the alternative failure is silent.

`test_a_hanging_toggle_is_not_a_confirmation` comes second, and is why the wrapper reads
the script's output rather than its exit status. `wine python` does not reliably exit when
the script does - the wineserver keeps the process alive - so `$?` would read a hang as a
pass, and the wrapper would hand the API a terminal it never heard from.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WRAPPER = HERE.parent.parent.parent / "assets" / "auto_login_wrapper.sh"

LOGIN_OK = "Auto-login sequence completed"
LOGIN_FAILED = "An error occurred"

#: What `toggle_algo.py` prints on each of its outcomes, copied from the script.
ALGO_ON = "AutoTrading is on (confirmed on attempt 1)."
ALGO_PRESSED = "AutoTrading is on."
ALGO_STUCK = "could not turn AutoTrading on - orders will be refused (10027)."
ALGO_NO_MT5 = "cannot reach MT5: No module named 'MetaTrader5'"

#: A `wine` that answers for whichever script it was handed, and records the fact.
#: Matching on the script name is the point: the wrapper must run the toggle only
#: after the login has reported success. It matches `"$*"` rather than `"$2"`
#: because the toggle is invoked `wine python -u <script>`.
STUB = """#!/bin/bash
case "$*" in
    *auto_login.py)
        printf '%s\\n' "$AUTO_LOGIN_SAYS"
        ;;
    *toggle_algo.py)
        echo called >> "$TOGGLE_CALLS"
        printf '%s\\n' "$TOGGLE_SAYS"
        [ -n "$TOGGLE_HANGS" ] && sleep "$TOGGLE_HANGS"
        ;;
esac
exit 0
"""


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Drive the real wrapper against a stub `wine`, and report what it decided."""
    # Everything this test owns lives under `boot/`. The autouse fixture in
    # `conftest.py` puts its own `login_complete` straight in `tmp_path` for the
    # connector to find, and sharing that name made every assertion about this
    # script's marker pass vacuously.
    boot = tmp_path / "boot"
    stubs = boot / "stubs"
    stubs.mkdir(parents=True)
    wine = stubs / "wine"
    wine.write_text(STUB)
    wine.chmod(0o755)

    marker = boot / "login_complete"
    calls = boot / "toggle_calls"

    def go(login_says=LOGIN_OK, toggle_says=ALGO_ON, hangs="", require_algo=None, rounds=1, wait=3):
        env = {
            "PATH": f"{stubs}:/usr/bin:/bin",
            "AUTO_LOGIN_SAYS": login_says,
            "TOGGLE_SAYS": toggle_says,
            "TOGGLE_HANGS": hangs,
            "TOGGLE_CALLS": str(calls),
            "AUTO_LOGIN_LOG": str(boot / "auto_login.log"),
            "TOGGLE_ALGO_LOG": str(boot / "toggle_algo.log"),
            "LOGIN_MARKER": str(marker),
            "ASSETS_DIR": str(boot),
            # The container waits 30s for VNC and this has no VNC at all.
            "VNC_WAIT": "1",
            "ALGO_ROUNDS": str(rounds),
            "ALGO_WAIT": str(wait),
        }
        if require_algo is not None:
            env["MT5_REQUIRE_ALGO"] = require_algo
        done = subprocess.run(
            ["bash", str(WRAPPER)], env=env, capture_output=True, text=True, timeout=180
        )
        return {
            "code": done.returncode,
            "out": done.stdout,
            "marker": marker.exists(),
            "toggles": len(calls.read_text().splitlines()) if calls.exists() else 0,
        }

    return go


def test_the_marker_waits_on_autotrading(run):
    """The ordinary boot: login, confirm, then write the marker."""
    got = run()
    assert got["code"] == 0
    assert got["marker"] is True
    assert got["toggles"] == 1
    assert "trade_allowed is true" in got["out"]


def test_a_press_that_takes_also_counts(run):
    """`toggle_algo.py` prints a different line when it had to press. Both are a pass."""
    got = run(toggle_says=ALGO_PRESSED)
    assert got["code"] == 0
    assert got["marker"] is True


def test_no_marker_when_autotrading_cannot_be_turned_on(run):
    """The one that matters: no confirmation, no marker, so the API never serves.

    `run-server.sh` waits on the marker forever, and the wrapper's supervisord program
    does not restart, so this leaves the container plainly down instead of quietly
    useless. That is the trade this change makes deliberately.
    """
    got = run(toggle_says=ALGO_STUCK, rounds=2)
    assert got["code"] == 1
    assert got["marker"] is False
    # Both rounds were spent trying, rather than failing on the first read.
    assert got["toggles"] == 2
    assert "10027" in got["out"]


def test_cannot_reach_mt5_is_not_a_confirmation(run):
    """The failure seen in the container: the toggle run under a python without the binding."""
    got = run(toggle_says=ALGO_NO_MT5)
    assert got["code"] == 1
    assert got["marker"] is False


def test_a_hanging_toggle_is_not_a_confirmation(run):
    """Silence is not a pass, and this is why the verdict comes from the log, not `$?`."""
    got = run(toggle_says="", hangs="20", wait=2)
    assert got["code"] == 1
    assert got["marker"] is False


def test_require_algo_zero_serves_reads(run):
    """The escape hatch: a deliberate read-only bridge still gets its marker."""
    got = run(toggle_says=ALGO_STUCK, require_algo="0")
    assert got["code"] == 0
    assert got["marker"] is True
    assert "serve reads" in got["out"]


def test_requiring_autotrading_is_the_default(run):
    """Unset means required. A deploy that forgets the variable must fail closed."""
    assert run(toggle_says=ALGO_STUCK, require_algo=None)["marker"] is False


def test_a_failed_login_never_reaches_the_toggle(run):
    """Pressing Ctrl+E at a login prompt is how the toggle ended up off in the first place."""
    got = run(login_says=LOGIN_FAILED)
    assert got["code"] == 1
    assert got["marker"] is False
    assert got["toggles"] == 0
