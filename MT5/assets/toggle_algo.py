"""Turn AutoTrading on, on a terminal that is already running.

`auto_login.py` does this once at start-up, as part of typing credentials into
a fresh GUI. This is the same thing for a terminal that is **already up** - the
case the start-up path cannot reach, and the one that cost nine hours on
2026-09-12 when the launcher's restart loop meant the start-up Ctrl+E never
landed on a window that lived long enough to act on it.

    wine python /root/toggle_algo.py

Two properties it does not share with a bare Ctrl+E:

* **It reads the state first and only presses when it needs to.** Ctrl+E is a
  toggle, so pressing it blind is a coin flip, and `enable_algo_trading` used
  to press three times on an unknown state - an odd number, which guarantees a
  net change from wherever you started. That is how AutoTrading ended up off.
* **It verifies against the terminal rather than its log.**
  `terminal_info().trade_allowed` is what the terminal will answer an order
  with. The log records a *transition*, so it says nothing at all about a
  terminal that has never toggled - on 2026-09-12 it held 20,998 lines with not
  one mention of trading while every order was being rejected.

Exit status is the answer: 0 when AutoTrading is on when this returns, 1 when
it is not. So it is usable from a healthcheck or a deploy step, not only by
hand.
"""

from __future__ import annotations

import os
import sys
import time

ATTEMPTS = 3
SETTLE = 0.5
WAIT = 15.0


def state() -> bool | None:
    """What the terminal will answer an order with, or None if it cannot say."""
    try:
        import MetaTrader5 as mt5

        info = mt5.terminal_info()
    except Exception:
        return None
    if info is None:
        return None
    allowed = getattr(info, "trade_allowed", None)
    return None if allowed is None else bool(allowed)


def press() -> None:
    """Ctrl+E into the VNC session, which is the only way to reach the toggle."""
    from vncdotool import api

    # The same two variables `auto_login.py` reads, and the same defaults. A
    # bare host means port 5900 to vncdotool; spelling it `localhost::5901`
    # here - which an earlier version of this file did - connects to nothing
    # and fails with a refusal that names the wrong problem.
    client = api.connect(
        os.environ.get("VNC_SERVER_HOST", "localhost"),
        password=os.environ.get("VNC_PASSWORD"),
    )
    try:
        client.keyDown("ctrl")
        client.keyPress("e")
        client.keyUp("ctrl")
        time.sleep(SETTLE)
    finally:
        client.disconnect()


def main() -> int:
    try:
        import MetaTrader5 as mt5

        mt5.initialize()
    except Exception as exc:
        print(f"cannot reach MT5: {exc}")
        return 1

    for attempt in range(1, ATTEMPTS + 1):
        now = state()
        if now is True:
            print(f"AutoTrading is on (confirmed on attempt {attempt}).")
            return 0
        if now is None:
            # Refusing to guess is the whole point - see the module docstring.
            print(f"cannot read trade_allowed (attempt {attempt}); not toggling.")
            time.sleep(2)
            continue

        print(f"AutoTrading is off; pressing Ctrl+E (attempt {attempt}).")
        press()

        deadline = time.time() + WAIT
        while time.time() < deadline:
            time.sleep(0.5)
            if state() is True:
                print("AutoTrading is on.")
                return 0

    print("could not turn AutoTrading on - orders will be refused (10027).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
