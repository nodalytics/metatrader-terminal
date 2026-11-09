"""The four things a client needs from this API and could not get.

Each of these is a case where the service had the answer and would not say it,
and in every one the symptom was a client doing the wrong thing confidently:

1. **A refusal and a bad API key were both 4xx** with the difference only in
   the text of `error`, so one client reported a broken deployment as an
   ordinary bad trade. `kind` is a closed set to branch on.
2. **`order_check` checked no order.** It returned `symbol_info` and ignored
   any order described to it, so "would this be accepted, and what margin does
   it need" had no answer and a client had to find out by trading.
3. **Nothing checked an order against the symbol's own limits**, although
   `symbol_info` carries all of them, so a volume off the step became
   `10014 Invalid volume` - a number that does not say which limit or what
   would be allowed.
4. **Two flags are spelled `trade_allowed`** and only one decides whether an
   order fills. `/account/health` has read `healthy` over a terminal that
   refused everything.
"""

from __future__ import annotations

import pytest

#: **Every `app.*` import is inside a test, not up here.** The `terminal`
#: fixture installs the fake `MetaTrader5` binding, and a module-scope import
#: runs at *collection* - before any fixture - so importing a service here
#: binds the real library, which is not installed. Eight of these tests failed
#: that way before the imports moved.

#: `XAUUSD`, because that is a symbol the fake terminal knows: `fake_mt5`
#: ships `XAUUSD` and `BTCUSD`, and `symbol_info` returns `None` for anything
#: else - which is a 404, not a validation failure.
ORDER = {"symbol": "XAUUSD", "volume": 0.1, "order_type": "BUY", "sl": 0.0}


class _Spec:
    """Just the volume fields `symbol_info` carries, for the validator."""

    def __init__(self, **kw):
        self.volume_min = kw.get("volume_min", 0.01)
        self.volume_max = kw.get("volume_max", 100.0)
        self.volume_step = kw.get("volume_step", 0.01)
        self.trade_stops_level = kw.get("trade_stops_level", 0)
        self.point = kw.get("point", 0.01)


# ───────────────────────────────── 1. the error envelope


def test_every_kind_is_in_the_closed_set():
    """A client is told to switch on `kind`, so a new class cannot invent one."""
    from app.utils import exceptions
    from app.utils.exceptions import KINDS

    classes = [
        v
        for v in vars(exceptions).values()
        if isinstance(v, type) and issubclass(v, exceptions.MT5BaseException)
    ]
    assert classes, "no exception classes found - the import moved"
    for cls in classes:
        assert cls.kind in KINDS, f"{cls.__name__} has kind {cls.kind!r}"


def test_a_refusal_is_retryable_only_when_sending_it_again_could_work():
    """**The distinction a client could not make at all.**

    Every refusal was one 400 with a number in it, so a client retried all of
    them - a loop that cannot succeed on `not enough money` - or none, giving
    up on a requote that clears in a second.
    """
    from app.utils.exceptions import MT5OrderError

    assert MT5OrderError("requote", code=10004).is_retryable() is True
    assert MT5OrderError("no quotes", code=10021).is_retryable() is True
    assert MT5OrderError("no money", code=10019).is_retryable() is False
    assert MT5OrderError("market closed", code=10018).is_retryable() is False
    # The control that matters most: 10027 *does* clear, but it clears when
    # somebody presses Ctrl+E, not with time. A retry loop on it is a
    # busy-wait on a human, and `/terminal/can-trade` is the route for it.
    assert MT5OrderError("autotrading off", code=10027).is_retryable() is False


def test_a_terminal_fault_is_retryable_and_a_refusal_is_not_by_default():
    """The two are different `kind`s because the remedies are different: one
    names a condition at the broker, the other says this request was never
    judged and may well be fine."""
    from app.utils.exceptions import MT5ConnectionError, MT5OrderError

    assert MT5ConnectionError("ipc down").kind == "terminal"
    assert MT5ConnectionError("ipc down").is_retryable() is True
    assert MT5OrderError("refused", code=10013).kind == "refusal"


def test_a_malformed_request_answers_with_kind_validation_and_keeps_detail(app_client):
    """**422 in the same envelope, with `detail` kept.**

    FastAPI's default body is `{"detail": [...]}` where everything else here is
    `{"error": ...}`, so a client parsed two shapes to learn what broke. The
    new fields are additive: `detail` still names the offending field, and
    three existing tests read it.
    """
    answer = app_client.post("/api/v1/trading/order", json={"symbol": "XAUUSD"})
    assert answer.status_code == 422
    body = answer.json()
    assert body["kind"] == "validation"
    assert body["retryable"] is False
    assert "detail" in body, "detail was dropped; existing clients read it"


def test_an_unauthenticated_call_is_kind_auth_and_not_a_trading_refusal(app_client):
    """**The one error a client must never read as "the broker said no".**

    Retrying it cannot help, and treating it as a refusal hides a
    misconfigured deploy behind a strategy that looks like it is losing.
    """
    answer = app_client.get("/api/v1/account/info", headers={"X-API-Key": "wrong"})
    if answer.status_code in (401, 403):
        body = answer.json()
        assert body["kind"] == "auth"
        assert body["retryable"] is False
    else:
        pytest.skip(f"auth is not enforced in this configuration ({answer.status_code})")


# ───────────────────────────────── 2. order_check that checks


def test_order_check_asks_the_terminal_about_the_order_it_would_send(app_client, terminal):
    """**It used to return `symbol_info` and ignore the order.**

    What is asserted is that `order_check` reached the terminal carrying the
    *order* - the symbol, the volume and the side - because a check built from
    its own copy of the request would answer about a different order and keep
    looking right while the two drifted.
    """
    answer = app_client.post("/api/v1/trading/order_check", json=ORDER)
    assert answer.status_code == 200, answer.text

    sent = [c for c in terminal.calls if c[0] == "order_check"]
    assert len(sent) == 1, f"order_check never reached the terminal: {terminal.calls}"
    # `fake_mt5._record` stores `(name, args, kwargs)`, so the request is the
    # first positional argument rather than the second element.
    request = sent[0][1][0]
    assert request["symbol"] == "XAUUSD"
    assert request["volume"] == pytest.approx(0.1)
    # No order was placed. The whole point of asking.
    assert not [c for c in terminal.calls if c[0] == "order_send"]


def test_order_check_and_order_send_build_the_same_request(terminal):
    """The check is worthless if it validates a different order.

    Asserted through the production builder rather than by comparing two
    literals, so a field added to one and not the other fails here.
    """
    from app.services.trade import trade_service

    first = trade_service.market_request(**ORDER)
    second = trade_service.market_request(**ORDER)
    assert first == second
    for field in ("action", "symbol", "volume", "type", "price", "sl", "type_filling"):
        assert field in first, f"{field} missing from the request both routes send"


def test_the_old_get_route_still_answers_with_the_specification(app_client):
    """The control: the misnamed route is documented, not broken.

    Callers depend on it and `test_order_check` in the integration suite
    asserts its shape, so it keeps its path and its payload.
    """
    answer = app_client.get("/api/v1/trading/order_check/XAUUSD")
    assert answer.status_code == 200
    assert "volume_min" in answer.json()


# ───────────────────────────────── 3. the symbol's own limits


def test_a_volume_the_symbol_rules_out_is_refused_before_the_terminal_sees_it():
    """Each of the three limits, and each says which one and what is allowed.

    `10014 Invalid volume` says none of that, and the caller then has to fetch
    `symbol_info` and work it out - the work this service exists to have done.
    """
    from app.services.trade import validate_against_symbol
    from app.utils.exceptions import MT5ValidationError

    spec = _Spec(volume_min=0.01, volume_max=5.0, volume_step=0.01)
    with pytest.raises(MT5ValidationError, match="volume_min"):
        validate_against_symbol("EURUSD", spec, volume=0.003)
    with pytest.raises(MT5ValidationError, match="volume_max"):
        validate_against_symbol("EURUSD", spec, volume=9.0)
    with pytest.raises(MT5ValidationError, match="volume_step"):
        validate_against_symbol("EURUSD", spec, volume=0.015)


def test_the_order_route_applies_the_symbol_limits_and_sends_nothing(app_client, terminal):
    """**That the validator is wired in, not merely that it exists.**

    The tests above call `validate_against_symbol` directly, which would keep
    passing if nothing ever called it - the gate would be correct and
    unreachable. This goes through the route, and asserts the order never
    reached the terminal: the point of validating is that MT5 is not asked.
    """
    answer = app_client.post("/api/v1/trading/order", json={**ORDER, "volume": 0.015})
    assert answer.status_code == 422, answer.text
    body = answer.json()
    assert body["kind"] == "validation"
    assert body["retryable"] is False
    assert "volume_step" in body["error"]
    assert not [c for c in terminal.calls if c[0] == "order_send"], "it was sent anyway"


def test_the_order_route_still_sends_a_volume_the_symbol_allows(app_client, terminal):
    """The control for the one above: the gate lets a good order through.

    Without it, "an off-step volume is refused" cannot be told from "the route
    refuses everything", which is the more expensive failure - it looks like a
    quiet market.
    """
    app_client.post("/api/v1/trading/order", json={**ORDER, "volume": 0.02})
    assert [c for c in terminal.calls if c[0] == "order_send"], "a valid order was not sent"


def test_the_step_check_does_not_refuse_volumes_every_broker_accepts():
    """**The control, and binary floating point is the reason it exists.**

    `0.03 % 0.01` is not zero - it is a number just under the step - so a
    modulo test would refuse 0.03 on a 0.01-step symbol, which is an ordinary
    three-micro-lot order. A tolerance against the step is what makes the gate
    let something through.
    """
    from app.services.trade import validate_against_symbol

    spec = _Spec(volume_min=0.01, volume_max=100.0, volume_step=0.01)
    for volume in (0.01, 0.02, 0.03, 0.07, 1.0, 2.5, 100.0):
        validate_against_symbol("EURUSD", spec, volume=volume)


def test_a_symbol_that_states_no_limits_is_not_second_guessed():
    """A missing specification means the terminal did not say, which is not
    the same as a limit of zero. Inventing one here would refuse every
    order on a symbol whose record is sparse."""
    from app.services.trade import validate_against_symbol

    validate_against_symbol("EURUSD", None, volume=0.1)
    validate_against_symbol("EURUSD", _Spec(volume_min=0.0, volume_max=0.0, volume_step=0.0), volume=7.0)


# ───────────────────────────────── 3b. the stops level


def test_a_stop_inside_the_brokers_floor_is_refused_with_its_own_retcode():
    """`trade_stops_level` is a hard floor and it is **not uniform**.

    Read off the live terminal on 2026-11-09, in points: Australia 200 **500**,
    Wall Street 30 300, US Tech 100 150, US SP 500 **50**. Five price points of
    required distance on one index against half a point on another, same broker
    and same clock. Inside it the order is refused `10016 Invalid stops`, a
    code naming neither the distance required nor which leg broke it.
    """
    from app.services.trade import validate_stops
    from app.utils.exceptions import MT5ValidationError

    spec = _Spec(trade_stops_level=500, point=0.01)  # Australia 200's floor: 5.00
    with pytest.raises(MT5ValidationError, match="sl") as caught:
        validate_stops("Australia 200", spec, price=8000.0, sl=7998.0)
    assert caught.value.code == 10016, "a client reads `code` to get a retcode"
    with pytest.raises(MT5ValidationError, match="tp"):
        validate_stops("Australia 200", spec, price=8000.0, tp=8001.0)


def test_the_floor_is_points_times_point_size_and_not_a_raw_point_count():
    """**The unit, asserted.** Comparing a price distance against a raw point
    count is the error that read a 476% round trip off a spread column once.

    A stop 4.00 away is inside Australia 200's 500-point floor (5.00) and
    outside US SP 500's 50-point floor (0.50). If the floor were read as raw
    points, both would be refused and the second assertion fails.
    """
    from app.services.trade import validate_stops
    from app.utils.exceptions import MT5ValidationError

    with pytest.raises(MT5ValidationError):
        validate_stops("Australia 200", _Spec(trade_stops_level=500, point=0.01),
                       price=8000.0, sl=7996.0)
    validate_stops("US SP 500", _Spec(trade_stops_level=50, point=0.01),
                   price=6800.0, sl=6796.0)


def test_a_stop_at_the_floor_and_an_unset_one_are_both_accepted():
    """**The control, and the second half of it is the important half.**

    `sl=0.0` is not a stop at price zero - it is how MT5 spells "no stop", and
    the bridge's market-order model *requires* `sl` and sends `0.0` when the
    caller named none. Refusing those would refuse every unprotected order,
    which is most of them. A distance exactly at the floor is allowed by the
    broker, so it must be allowed here.
    """
    from app.services.trade import validate_stops

    spec = _Spec(trade_stops_level=150, point=0.01)  # US Tech 100: 1.50
    validate_stops("US Tech 100", spec, price=25000.0, sl=24998.5)  # exactly 1.50
    validate_stops("US Tech 100", spec, price=25000.0, sl=24990.0)  # well outside
    validate_stops("US Tech 100", spec, price=25000.0, sl=0.0)      # no stop
    validate_stops("US Tech 100", spec, price=25000.0, sl=None, tp=None)
    # No floor stated, and no specification at all: the terminal did not say,
    # which is not a floor of zero to enforce.
    validate_stops("X", _Spec(trade_stops_level=0, point=0.01), price=100.0, sl=99.999)
    validate_stops("X", None, price=100.0, sl=99.999)


def test_the_order_route_applies_the_stops_floor_and_sends_nothing(app_client, terminal):
    """That it is **wired**, like the volume check - and that MT5 is not asked.

    The fake symbol ships `trade_stops_level=0`, so the floor is set on it here
    rather than relying on a default that would make this pass for the wrong
    reason.
    """
    from tests.unit import fake_mt5

    terminal.symbols["XAUUSD"] = fake_mt5.symbol(trade_stops_level=500, point=0.01)
    tick = terminal.ticks["XAUUSD"]
    answer = app_client.post(
        "/api/v1/trading/order", json={**ORDER, "sl": float(tick.ask) - 0.5}
    )
    assert answer.status_code == 422, answer.text
    body = answer.json()
    assert body["kind"] == "validation"
    assert body["code"] == 10016
    assert not [c for c in terminal.calls if c[0] == "order_send"], "it was sent anyway"


# ───────────────────────────────── 4. one can-I-trade answer


def test_can_trade_is_false_with_a_reason_when_autotrading_is_off(app_client, terminal):
    """**The outage this route exists for.**

    On 2026-11-04 `/account/health` said healthy, `/terminal/ping` answered
    200 and `account_info().trade_allowed` said `True`, while AutoTrading was
    off in the client terminal and every order came back `10027`. Only
    `terminal_info().trade_allowed` says so, and nothing consulted it.
    """
    terminal.trade_allowed = False
    body = app_client.get("/api/v1/terminal/can-trade").json()
    assert body["can_trade"] is False
    assert "autotrading" in body["reason"].lower()
    # Both inputs are returned, so a caller can still see which one is false.
    assert body["terminal_trade_allowed"] is False
    assert body["account_trade_allowed"] is True


def test_can_trade_is_true_when_both_flags_agree(app_client, terminal):
    """The control. Without it, "it reports false" cannot be told from "it
    always reports false", and a client would never place an order."""
    terminal.trade_allowed = True
    body = app_client.get("/api/v1/terminal/can-trade").json()
    assert body["can_trade"] is True
    assert body["reason"] == ""


def test_asking_whether_you_can_trade_is_never_an_error(app_client, terminal):
    """200 even when the answer is no.

    A client monitoring tradability should not have to treat an error response
    as data; "no, and here is why" is the successful outcome of asking.
    """
    terminal.trade_allowed = False
    assert app_client.get("/api/v1/terminal/can-trade").status_code == 200


def test_health_reports_tradability_without_changing_what_status_means(app_client, terminal):
    """`status` keeps its three values because healthchecks compare against
    them; `can_trade` is the separate fact reported beside it.

    Folding "cannot trade" into `status: unhealthy` would restart the container
    over a terminal quirk a Ctrl+E fixes.
    """
    terminal.trade_allowed = False
    body = app_client.get("/api/v1/account/health").json()
    assert body["status"] == "healthy", "connected is still healthy for this process"
    assert body["can_trade"] is False, "health said nothing about trading again"
    assert body["reason"]
