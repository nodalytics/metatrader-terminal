"""Three defects found while writing a client against this API.

Each of these is written to fail against the code as it was, so that the fix
is demonstrated rather than asserted.
"""

from __future__ import annotations

import fake_mt5


def test_positions_can_be_filtered_by_magic(app_client, terminal):
    """`positions_get` does not take a `magic` keyword, and never has.

    It filters by `symbol`, `group` or `ticket`. Passing `magic` to the real
    package raises, so `GET /positions/?magic=...` — the call any bot makes to
    find *its own* trades — failed outright. The fake refuses the same keyword,
    which is what makes this test able to catch it.
    """
    terminal.positions = [
        fake_mt5.position(ticket=1, magic=777701),
        fake_mt5.position(ticket=2, magic=0),
        fake_mt5.position(ticket=3, magic=777701),
    ]
    response = app_client.get("/api/v1/positions/", params={"magic": 777701})
    assert response.status_code == 200
    tickets = sorted(row["ticket"] for row in response.json())
    assert tickets == [1, 3]


def test_positions_without_a_magic_returns_everything(app_client, terminal):
    terminal.positions = [
        fake_mt5.position(ticket=1, magic=777701),
        fake_mt5.position(ticket=2, magic=0),
    ]
    response = app_client.get("/api/v1/positions/")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_a_stop_can_be_moved_by_ticket(app_client, terminal):
    """Trailing a stop needs the position's ticket and nothing else.

    The only route for this took a `trade_id` — a row in this service's own
    database — and looked the ticket up from it. A position opened by anything
    other than this service therefore could not have its stop moved at all,
    which is every position after a redeploy, and every one placed by a bot
    that talks to `/trading/order` without recording the row it got back.
    """
    terminal.positions = [fake_mt5.position(ticket=42, magic=777701)]
    response = app_client.post(
        "/api/v1/positions/modify",
        json={"ticket": 42, "sl": 4398.0, "tp": 4410.0},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    sent = [call for call in terminal.calls if call[0] == "order_send"]
    assert sent, "no order was sent to the terminal"
    request = sent[-1][1][0]
    assert request["action"] == fake_mt5.TRADE_ACTION_SLTP
    assert request["position"] == 42
    assert request["sl"] == 4398.0
    assert request["tp"] == 4410.0


def test_moving_a_stop_on_an_unknown_ticket_is_a_404(app_client, terminal):
    terminal.positions = []
    response = app_client.post(
        "/api/v1/positions/modify", json={"ticket": 999, "sl": 1.0}
    )
    assert response.status_code in (400, 404)


def test_the_recorded_leverage_is_the_accounts_not_a_constant(app_client, terminal):
    """It was hardcoded to 500, so every stored trade's capital was wrong.

    `capital` is `notional / leverage`, and it is what the row reports as the
    money the trade tied up. On this account — leverage 200 — the constant
    understated it by a factor of two and a half, silently, in the table
    somebody would later use to work out what the strategy cost to run.
    """
    response = app_client.post(
        "/api/v1/trading/order",
        json={"symbol": "XAUUSD", "volume": 0.05, "order_type": "BUY", "sl": 4395.0},
    )
    assert response.status_code == 201
    trade = response.json()["trade"]
    assert trade["leverage"] == fake_mt5.account().leverage


def test_an_order_returns_the_tickets_and_the_retcode(app_client, terminal):
    """The response has to carry what the terminal said, and it did not.

    `POST /trading/order` returned `{"success": true, "trade": <row>}` and threw
    the MT5 result away — no ticket, no retcode, no fill price — so a client had
    to infer the position it had just opened from the database row's
    `transaction_broker_id`.

    Worse, the row itself serialised to `{}`. The route declares no
    `response_model`, and a SQLModel table instance encoded without one comes
    back empty on the versions this project installs (nothing here is pinned).
    So the response was `{"success": true, "trade": {}}` and a caller learned
    nothing at all about the order it had just placed.
    """
    terminal.next_result = fake_mt5.order_result(order=98765, price=4400.75)
    response = app_client.post(
        "/api/v1/trading/order",
        json={"symbol": "XAUUSD", "volume": 0.05, "order_type": "BUY", "sl": 4395.0},
    )
    assert response.status_code == 201
    body = response.json()

    assert body["result"]["order"] == 98765
    assert body["result"]["retcode"] == fake_mt5.TRADE_RETCODE_DONE
    assert body["result"]["price"] == 4400.75

    assert body["trade"], "the trade row serialised to nothing"
    assert body["trade"]["symbol"] == "XAUUSD"
    assert body["trade"]["transaction_broker_id"] == "98765"


def test_a_rejected_login_does_not_restart_the_server():
    """Found against a live Deriv demo, and it cost an hour to see.

    MT5's own log said `'6258778': authorization on Deriv-Demo failed (Invalid
    account)` — a precise, actionable answer. What the HTTP client got was
    `connection reset by peer` on every MT5-backed route, because the connector
    treated *any* initialisation failure as a wedged IPC pipe, called
    `os._exit(1)` mid-response, and let supervisor restart it. Round and round,
    with the real reason only ever written to a file inside the container.

    A restart cures a broken pipe. It cannot cure a wrong password.
    """
    from app.services.connector import restart_helps

    # Worth restarting for: the pipe really may be wedged.
    assert restart_helps(-10005)  # RES_E_INTERNAL_FAIL_CONNECT
    assert restart_helps(-10006)  # RES_E_INTERNAL_FAIL_TIMEOUT
    assert restart_helps(-1)      # generic failure

    # Not worth restarting for: identical outcome next time round.
    assert not restart_helps(-6)  # authorization failed
    assert not restart_helps(-8)  # algo trading disabled
    assert not restart_helps(-5)  # invalid version
    assert not restart_helps(-2)  # invalid params


def test_the_terminals_refusal_reaches_the_caller(app_client, terminal, monkeypatch):
    """A 503 naming the reason beats a socket that closes."""
    from app.services import connector

    monkeypatch.setattr(connector.mt5_connector, "_initialized", False)
    monkeypatch.setattr(
        connector.mt5_connector, "_last_error", (-6, "Terminal: Authorization failed")
    )
    response = app_client.get("/api/v1/positions/")
    assert response.status_code == 503
    detail = response.json().get("detail") or response.text
    assert "Authorization failed" in str(detail)
    assert "MT5_LOGIN" in str(detail)
