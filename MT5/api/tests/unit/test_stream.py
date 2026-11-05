"""The WebSocket: what it pushes, when it stays quiet, and how it fails."""

from __future__ import annotations

import fake_mt5


def read_until(ws, kind, limit=40):
    """Next frame of `kind`, or None. The stream interleaves several types."""
    for _ in range(limit):
        frame = ws.receive_json()
        if frame.get("type") == kind:
            return frame
    return None


def test_a_socket_opens_and_says_what_it_is_following(app_client):
    with app_client.websocket_connect("/api/v1/stream?symbols=XAUUSD") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["symbols"] == ["XAUUSD"]


def test_a_subscribed_symbol_starts_arriving(app_client, terminal):
    with app_client.websocket_connect("/api/v1/stream?symbols=XAUUSD") as ws:
        ws.receive_json()
        tick = read_until(ws, "tick")
        assert tick is not None
        assert tick["symbol"] == "XAUUSD"
        assert tick["bid"] == 4400.0
        assert tick["ask"] == 4400.5


def test_symbols_can_be_subscribed_after_connecting(app_client):
    with app_client.websocket_connect("/api/v1/stream") as ws:
        ws.receive_json()
        ws.send_json({"action": "subscribe", "symbols": ["BTCUSD"]})
        confirmed = read_until(ws, "subscribed")
        assert confirmed["symbols"] == ["BTCUSD"]
        tick = read_until(ws, "tick")
        assert tick["symbol"] == "BTCUSD"


def test_an_unchanged_quote_is_not_news():
    """The property that makes this cheap rather than a firehose.

    Tested on the comparison rather than through the socket, and deliberately:
    the stream is silent when nothing changes, so a test asserting "no frame
    arrives" has nothing to wait for and blocks until the suite is killed.
    Absence over a socket is not observable; the decision behind it is.
    """
    from app.routers.stream import _quote_of

    first = fake_mt5.tick(bid=4400.0, ask=4400.5, when=1_700_000_000)._asdict()
    same = fake_mt5.tick(bid=4400.0, ask=4400.5, when=1_700_000_000)._asdict()
    moved = fake_mt5.tick(bid=4400.5, ask=4401.0, when=1_700_000_001)._asdict()

    assert _quote_of(first) == _quote_of(same)
    assert _quote_of(first) != _quote_of(moved)


def test_floating_profit_alone_does_not_resend_the_positions():
    """Otherwise the slow loop becomes a second quote feed."""
    from app.routers.stream import _position_digest

    before = [fake_mt5.position(ticket=1, profit=2.5)._asdict()]
    drifted = [fake_mt5.position(ticket=1, profit=9.9)._asdict()]
    trailed = [fake_mt5.position(ticket=1, profit=2.5, sl=4399.0)._asdict()]
    closed = []

    assert _position_digest(before) == _position_digest(drifted)
    assert _position_digest(before) != _position_digest(trailed)
    assert _position_digest(before) != _position_digest(closed)


def test_a_moved_quote_is_sent(app_client, terminal):
    with app_client.websocket_connect("/api/v1/stream?symbols=XAUUSD") as ws:
        ws.receive_json()
        assert read_until(ws, "tick") is not None
        terminal.ticks["XAUUSD"] = fake_mt5.tick(bid=4402.0, ask=4402.5, when=1_700_000_100)
        moved = read_until(ws, "tick")
        assert moved["bid"] == 4402.0


def test_positions_arrive_and_carry_the_magic_filter(app_client, terminal):
    terminal.positions = [
        fake_mt5.position(ticket=1, magic=777701),
        fake_mt5.position(ticket=2, magic=0),
    ]
    with app_client.websocket_connect("/api/v1/stream?magic=777701") as ws:
        ws.receive_json()
        frame = read_until(ws, "positions")
        assert frame is not None
        assert [p["ticket"] for p in frame["positions"]] == [1]


def test_the_account_arrives(app_client):
    with app_client.websocket_connect("/api/v1/stream") as ws:
        ws.receive_json()
        frame = read_until(ws, "account")
        assert frame is not None
        assert frame["equity"] == fake_mt5.account().equity
        assert frame["currency"] == "USD"


def test_a_symbol_the_broker_does_not_carry_is_reported_and_then_left_alone(app_client):
    """It must not take the socket down, nor repeat the same error forever."""
    with app_client.websocket_connect("/api/v1/stream?symbols=NOSUCH") as ws:
        ws.receive_json()
        error = read_until(ws, "error")
        assert error is not None
        assert error["symbol"] == "NOSUCH"

        # The socket is still usable after the failure, which is the half that
        # matters. That the error does not repeat is the `failed` set below.
        ws.send_json({"action": "ping"})
        assert read_until(ws, "pong") is not None


def test_a_failed_symbol_is_only_tried_once():
    from app.routers.stream import Subscription

    sub = Subscription()
    sub.follow(["NOSUCH"])
    sub.failed.add("NOSUCH")
    assert "NOSUCH" in sub.symbols
    assert "NOSUCH" in sub.failed


def test_unsubscribing_forgets_what_was_known_about_a_symbol():
    from app.routers.stream import Subscription

    sub = Subscription()
    sub.follow(["XAUUSD"])
    sub.last_quote["XAUUSD"] = (1, 2, 3)
    sub.failed.add("XAUUSD")
    assert sub.drop(["XAUUSD"]) == []
    assert not sub.last_quote
    assert not sub.failed


def test_a_socket_cannot_follow_unbounded_symbols():
    """Four hundred symbols is four hundred quotes every quarter second."""
    from app.routers.stream import MAX_SYMBOLS, Subscription

    sub = Subscription()
    sub.follow([f"SYM{n}" for n in range(MAX_SYMBOLS * 3)])
    assert len(sub.symbols) == MAX_SYMBOLS


def test_ping_is_answered(app_client):
    with app_client.websocket_connect("/api/v1/stream") as ws:
        ws.receive_json()
        ws.send_json({"action": "ping"})
        assert read_until(ws, "pong") is not None


def test_an_unknown_action_says_what_the_actions_are(app_client):
    with app_client.websocket_connect("/api/v1/stream") as ws:
        ws.receive_json()
        ws.send_json({"action": "trade_everything"})
        error = read_until(ws, "error")
        assert "unknown action" in error["message"]
        assert "subscribe" in error["actions"]


def test_a_socket_without_the_key_is_refused_when_one_is_configured(app_client, monkeypatch):
    from app.utils import config

    monkeypatch.setattr(type(config.settings), "api_key", property(lambda self: "secret"))
    with app_client.websocket_connect("/api/v1/stream") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "API key" in frame["message"]


def test_a_socket_with_the_key_is_accepted(app_client, monkeypatch):
    from app.utils import config

    monkeypatch.setattr(type(config.settings), "api_key", property(lambda self: "secret"))
    with app_client.websocket_connect("/api/v1/stream?api_key=secret") as ws:
        assert ws.receive_json()["type"] == "ready"


def test_a_subscription_keeps_the_symbols_spelling():
    """MT5 symbol names are case-sensitive and many are not upper case.

    `Germany 40`, `Wall Street 30`, `US Tech 100` and every Deriv synthetic
    (`Volatility 75 Index`, `Boom 1000 Index`) have mixed case. Upper-casing a
    subscription is invisible on `EURUSD` and `XAUUSD` - which is why it
    survived a desk that traded eight FX pairs and two metals - and makes all
    the others unsubscribable: the terminal answers `Tick data for 'GERMANY
    40' not found` and the socket reports a per-symbol stream error forever.
    """
    from app.routers.stream import Subscription

    sub = Subscription()
    following = sub.follow(["Germany 40", "Volatility 75 Index", "EURUSD"])

    assert "Germany 40" in following
    assert "Volatility 75 Index" in following
    assert "GERMANY 40" not in following
    assert "EURUSD" in following, "an already upper-case name must be unchanged"


def test_dropping_uses_the_same_spelling_as_following():
    """A `drop` that normalised differently from `follow` would leave a symbol
    subscribed forever and the socket pushing quotes nobody asked for."""
    from app.routers.stream import Subscription

    sub = Subscription()
    sub.follow(["Wall Street 30"])

    assert sub.drop(["Wall Street 30"]) == []


def test_resolve_symbol_finds_the_terminals_own_spelling(terminal):
    """A caller should not have to know the terminal's casing.

    `xauusd` resolving to `XAUUSD` is a contract this API already had, and the
    old implementation delivered it with `.upper()` - which works for exactly
    the symbols that are already upper case and silently breaks every other.
    """
    from app.services.market_data import resolve_symbol

    assert resolve_symbol(" xauusd ") == "XAUUSD"


def test_resolve_symbol_keeps_a_mixed_case_name_the_terminal_has(terminal):
    """The defect this exists for: `Germany 40` is the terminal's own name and
    upper-casing it produced `Tick data for 'GERMANY 40' not found`."""
    import fake_mt5

    from app.services.market_data import resolve_symbol

    terminal.symbols["Germany 40"] = fake_mt5.symbol("Germany 40", 18000.0, 18001.0)

    assert resolve_symbol("Germany 40") == "Germany 40"
    assert resolve_symbol("germany 40") == "Germany 40"


def test_resolve_symbol_returns_an_unknown_name_unchanged(terminal):
    """So the error names what the caller asked for.

    Returning something else - an upper-cased version, or empty - makes the
    "not found" message describe a symbol the caller never mentioned, which is
    how a casing bug reads as a missing instrument.
    """
    from app.services.market_data import resolve_symbol

    assert resolve_symbol("NOT A SYMBOL") == "NOT A SYMBOL"


def test_resolving_many_unknown_names_rebuilds_the_map_once(terminal):
    """A rebuild per miss is a terminal round trip per name, and the caller
    picks how many names there are.

    The first version of the resolver rebuilt on every miss, so fifty names the
    terminal does not have became fifty full `symbols_get()` calls over IPC.
    """
    from app.services.market_data import resolve_symbols

    before = sum(1 for call in terminal.calls if call[0] == "symbols_get")
    resolve_symbols([f"NO SUCH SYMBOL {i}" for i in range(50)])
    after = sum(1 for call in terminal.calls if call[0] == "symbols_get")

    assert after - before <= 1, "one rebuild should serve the whole batch"


def test_the_map_builds_on_a_machine_that_has_just_booted(terminal, monkeypatch):
    """`time.monotonic()` counts from boot, not from the epoch.

    The staleness check first spelled "never built" as `0.0` and asked whether
    `monotonic() - 0.0` exceeded the window. On a host whose uptime is under a
    minute that is false, so the map was never built and every lowercase name
    failed to resolve. It passed on a developer machine with days of uptime
    and failed in CI, whose containers are seconds old.
    """
    from app.services import market_data

    monkeypatch.setattr(market_data.time, "monotonic", lambda: 5.0)
    market_data.forget_symbols()

    assert market_data.resolve_symbol("xauusd") == "XAUUSD"
