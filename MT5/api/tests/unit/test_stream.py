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
