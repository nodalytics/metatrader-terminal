"""The AutoTrading toggle, the health payload, and the batch tick route.

All three exist because of the same outage, on 2026-09-24: the terminal was
attached and answering, `/health` said `ok`, and every order came back with
retcode 10027 `AutoTrading disabled by client`. Nothing in the service could
either report that or fix it.

The toggle tests do not go near VNC. `algo.press` is replaced with a function
that records the call and moves the fake terminal's state, which is what a real
Ctrl+E does. What is under test is the decision - *whether* to press, how many
times, and what to do when the state cannot be read - because that is where the
bug was: an earlier version pressed three times on an unknown state, an odd
number, which guaranteed a net change from wherever it started.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def presses(monkeypatch, terminal):
    """Replace the VNC keypress with a recorder that flips the fake's state."""
    from app.services import algo

    calls: list[bool] = []

    def fake_press() -> None:
        calls.append(True)
        terminal.trade_allowed = not terminal.trade_allowed

    monkeypatch.setattr(algo, "press", fake_press)
    return calls


# ------------------------------------------------------------------ reading


def test_reports_current_state(app_client, terminal):
    terminal.trade_allowed = True
    r = app_client.get("/api/v1/terminal/algo-trading")
    assert r.status_code == 200
    assert r.json() == {"trade_allowed": True}


def test_reports_off_as_off(app_client, terminal):
    terminal.trade_allowed = False
    r = app_client.get("/api/v1/terminal/algo-trading")
    assert r.status_code == 200
    assert r.json() == {"trade_allowed": False}


def test_unreadable_state_is_not_reported_as_off(app_client, terminal):
    """`None` must not collapse into `False`.

    They call for opposite actions: off means press, unknown means refuse to.
    """
    terminal.terminal_info_none = True
    r = app_client.get("/api/v1/terminal/algo-trading")
    assert r.status_code >= 400


# ------------------------------------------------------------------ setting


def test_enabling_when_already_on_presses_nothing(app_client, terminal, presses):
    """The idempotence that makes this safe to call on every deploy."""
    terminal.trade_allowed = True
    r = app_client.post("/api/v1/terminal/algo-trading", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"trade_allowed": True, "changed": False, "presses": 0}
    assert presses == []


def test_enabling_when_off_presses_exactly_once(app_client, terminal, presses):
    """One press, not three. Ctrl+E is a toggle."""
    terminal.trade_allowed = False
    r = app_client.post("/api/v1/terminal/algo-trading", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"trade_allowed": True, "changed": True, "presses": 1}
    assert len(presses) == 1
    assert terminal.trade_allowed is True


def test_disabling_when_on_presses_once(app_client, terminal, presses):
    terminal.trade_allowed = True
    r = app_client.post("/api/v1/terminal/algo-trading", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"trade_allowed": False, "changed": True, "presses": 1}
    assert terminal.trade_allowed is False


def test_disabling_when_already_off_presses_nothing(app_client, terminal, presses):
    terminal.trade_allowed = False
    r = app_client.post("/api/v1/terminal/algo-trading", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["changed"] is False
    assert presses == []


def test_defaults_to_enabling(app_client, terminal, presses):
    """An empty body means "turn it on", which is the reason anyone calls this."""
    terminal.trade_allowed = False
    r = app_client.post("/api/v1/terminal/algo-trading", json={})
    assert r.status_code == 200
    assert r.json()["trade_allowed"] is True


def test_refuses_to_press_on_an_unknown_state(app_client, terminal, presses):
    """The regression test for the bug that turned AutoTrading off.

    When the terminal will not say what the toggle is, pressing is a coin flip.
    """
    terminal.terminal_info_none = True
    r = app_client.post("/api/v1/terminal/algo-trading", json={"enabled": True})
    assert r.status_code == 409
    assert presses == []
    assert "unknown" in r.json()["detail"].lower()


def test_a_press_that_does_not_take_is_a_conflict(app_client, terminal, monkeypatch):
    """A GUI that ignores the keystroke must not be reported as success."""
    from app.services import algo

    monkeypatch.setattr(algo, "press", lambda: None)  # never moves the state
    monkeypatch.setattr(algo, "WAIT", 0.05)
    monkeypatch.setattr(algo, "POLL", 0.01)
    terminal.trade_allowed = False

    r = app_client.post("/api/v1/terminal/algo-trading", json={"enabled": True})
    assert r.status_code == 409
    assert "10027" in r.json()["detail"]


def test_retries_before_giving_up(app_client, terminal, monkeypatch):
    """The press is asynchronous, so one slow round must not be a failure."""
    from app.services import algo

    calls: list[int] = []

    def stubborn() -> None:
        calls.append(1)
        if len(calls) >= 2:  # takes on the second attempt
            terminal.trade_allowed = True

    monkeypatch.setattr(algo, "press", stubborn)
    monkeypatch.setattr(algo, "WAIT", 0.05)
    monkeypatch.setattr(algo, "POLL", 0.01)
    terminal.trade_allowed = False

    r = app_client.post("/api/v1/terminal/algo-trading", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"trade_allowed": True, "changed": True, "presses": 2}


# ------------------------------------------------------------------- health


def test_health_reports_the_terminal(app_client, terminal):
    terminal.trade_allowed = True
    terminal.connected = True
    r = app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["connected"] is True
    assert body["trade_allowed"] is True
    assert body["build"] == terminal.build


def test_health_exposes_a_blocked_terminal(app_client, terminal):
    """The outage this field exists for: attached, healthy, refusing orders."""
    terminal.trade_allowed = False
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["connected"] is True
    assert r.json()["trade_allowed"] is False


def test_health_survives_a_missing_terminal(app_client, terminal):
    """It must report the absence rather than 500 over it."""
    terminal.terminal_info_none = True
    r = app_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["connected"] is False
    assert body["trade_allowed"] is False
    assert body["build"] is None


# -------------------------------------------------------------- batch ticks


def test_batch_ticks_returns_every_symbol(app_client, terminal):
    r = app_client.get("/api/v1/symbols/ticks", params={"symbols": "XAUUSD,BTCUSD"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["ticks"]) == {"XAUUSD", "BTCUSD"}
    assert body["errors"] == {}
    assert body["requested"] == 2


def test_batch_ticks_is_partial_rather_than_all_or_nothing(app_client, terminal):
    """One unknown symbol must not cost the caller the other one."""
    r = app_client.get("/api/v1/symbols/ticks", params={"symbols": "XAUUSD,NOPE"})
    assert r.status_code == 200
    body = r.json()
    assert "XAUUSD" in body["ticks"]
    assert "NOPE" in body["errors"]
    assert "NOPE" not in body["ticks"]


def test_batch_ticks_with_nothing_resolving_is_still_an_answer(app_client, terminal):
    r = app_client.get("/api/v1/symbols/ticks", params={"symbols": "NOPE,ALSONOPE"})
    assert r.status_code == 200
    assert r.json()["ticks"] == {}
    assert len(r.json()["errors"]) == 2


def test_batch_ticks_normalises_case_and_whitespace(app_client, terminal):
    r = app_client.get("/api/v1/symbols/ticks", params={"symbols": " xauusd , btcusd "})
    assert r.status_code == 200
    assert set(r.json()["ticks"]) == {"XAUUSD", "BTCUSD"}


def test_batch_ticks_deduplicates(app_client, terminal):
    r = app_client.get("/api/v1/symbols/ticks", params={"symbols": "XAUUSD,XAUUSD"})
    assert r.status_code == 200
    assert r.json()["requested"] == 1


def test_batch_ticks_rejects_an_empty_list(app_client, terminal):
    r = app_client.get("/api/v1/symbols/ticks", params={"symbols": " , "})
    assert r.status_code == 422


def test_batch_ticks_caps_the_batch(app_client, terminal):
    from app.routers.symbols import MAX_TICK_BATCH

    many = ",".join(f"SYM{i}" for i in range(MAX_TICK_BATCH + 1))
    r = app_client.get("/api/v1/symbols/ticks", params={"symbols": many})
    assert r.status_code == 422
    assert str(MAX_TICK_BATCH) in r.json()["detail"]


def test_batch_ticks_does_not_shadow_the_single_route(app_client, terminal):
    """`/ticks` and `/ticks/{symbol}` must both still resolve."""
    one = app_client.get("/api/v1/symbols/ticks/XAUUSD")
    assert one.status_code == 200
    many = app_client.get("/api/v1/symbols/ticks", params={"symbols": "XAUUSD"})
    assert many.status_code == 200
    assert many.json()["ticks"]["XAUUSD"]["bid"] == one.json()["bid"]
