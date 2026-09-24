"""Reading and setting the terminal's AutoTrading toggle.

## Why this cannot be done through the terminal's API

`MetaTrader5` exposes `terminal_info().trade_allowed` and no way to change it.
The toggle is a property of the **GUI** - the Algo Trading button, Ctrl+E - and
MetaQuotes offers no programmatic setter, deliberately: the point of the switch
is that a human decided a robot may trade. So the only way to move it is to
send the keystroke into the session the terminal is drawing on, which is what
`vncdotool` is for and why it is already in `requirements.txt`.

That makes this the one endpoint in the service whose implementation is a
synthetic keypress. It is worth stating plainly rather than hiding, because the
failure modes are the failure modes of a GUI: the window can be missing, the
VNC password can be wrong, and the press can land somewhere else.

## Why the state is read first, and why that is not an optimisation

**Ctrl+E is a toggle, not a setter.** Pressing it blind is a coin flip on an
unknown state, and an earlier version of the start-up path pressed it *three*
times - an odd number, so it guaranteed a net change from wherever it started.
That is how AutoTrading came to be off on a terminal whose logs said it had
been enabled.

So `apply` reads `trade_allowed`, compares it to what was asked, and presses
only on a genuine mismatch. Asking for a state the terminal is already in does
nothing at all and reports `changed: false`. That makes the endpoint safe to
call on every deploy, which is the point - a deploy step that is only safe
sometimes will eventually run at the wrong time.

**And it verifies against the terminal, not against the log.**
`terminal_info().trade_allowed` is the thing an order is actually answered
with. The terminal's log records a *transition*, so it is silent about a
terminal that has never toggled: on 2026-09-12 it held 20,998 lines with no
mention of trading at all while every order was being rejected with 10027.

## Its relationship to `assets/toggle_algo.py`

That script does the same thing from a shell and this module does not import
it, which is duplication with a reason. It is the **break-glass** path: it has
to work when this service will not start, which is exactly when a wedged
AutoTrading toggle is most likely to be the reason. A shared import would make
the recovery tool depend on the thing being recovered.

The algorithm is the same in both, and a change to one belongs in the other.
"""

from __future__ import annotations

import logging
import os
import time

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

#: How many read-press-verify rounds before giving up. Three, because the press
#: is asynchronous: the terminal redraws and updates `trade_allowed` on its own
#: schedule, and a single round that read too early would report a failure that
#: the next read contradicts.
ATTEMPTS = 3

#: Let the keypress settle before releasing the VNC connection. Dropping the
#: connection immediately after `keyUp` has been observed to lose the event.
SETTLE = 0.5

#: How long to wait for `trade_allowed` to follow a press, per attempt.
WAIT = 15.0

#: How often to re-read while waiting.
POLL = 0.5


class AlgoToggleError(RuntimeError):
    """The toggle could not be moved, or its state could not be established.

    Separate from a connection error because the terminal is answering
    perfectly well - it is the GUI that would not cooperate.
    """


def state() -> bool | None:
    """What the terminal will answer an order with, or `None` if it cannot say.

    `None` is not "off". It means `terminal_info()` gave us nothing, and the
    difference matters: refusing to act on an unknown state is what stops the
    blind-press bug from coming back.
    """
    try:
        info = mt5.terminal_info()
    except Exception:  # pragma: no cover - the binding raising is environmental
        return None
    if info is None:
        return None
    allowed = getattr(info, "trade_allowed", None)
    return None if allowed is None else bool(allowed)


def press() -> None:
    """Send Ctrl+E into the VNC session the terminal is drawing on."""
    from vncdotool import api

    # The same two variables `auto_login.py` and `toggle_algo.py` read, with
    # the same defaults. A bare host means port 5900 to vncdotool; spelling it
    # `localhost::5901` connects to nothing and fails with a refusal that names
    # the wrong problem.
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


def apply(enabled: bool) -> dict[str, object]:
    """Put AutoTrading into `enabled`, pressing only if it is not there already.

    Returns the outcome rather than raising on a no-op, because "it was already
    on" is a success and the caller usually wants to know which it was.

    Raises `AlgoToggleError` when the state cannot be read at all, or when the
    press did not take within `ATTEMPTS` rounds.
    """
    before = state()
    if before is None:
        raise AlgoToggleError(
            "cannot read trade_allowed from the terminal, so refusing to press "
            "a toggle whose current state is unknown"
        )
    if before is enabled:
        return {"trade_allowed": before, "changed": False, "presses": 0}

    presses = 0
    for attempt in range(1, ATTEMPTS + 1):
        logger.info(
            f"AutoTrading is {'on' if not enabled else 'off'}; "
            f"pressing Ctrl+E to turn it {'on' if enabled else 'off'} "
            f"(attempt {attempt}/{ATTEMPTS})"
        )
        press()
        presses += 1

        deadline = time.time() + WAIT
        while time.time() < deadline:
            time.sleep(POLL)
            if state() is enabled:
                logger.info(f"AutoTrading is now {'on' if enabled else 'off'}")
                return {"trade_allowed": enabled, "changed": True, "presses": presses}

    raise AlgoToggleError(
        f"pressed Ctrl+E {presses} time(s) and trade_allowed is still "
        f"{state()!r} - orders will be refused with 10027"
    )
