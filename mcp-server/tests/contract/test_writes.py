"""What reaches the terminal when an order is placed, moved or closed.

The bridge's write routes are not uniform and the differences are silent.
`/positions/close` and `/positions/close_all` read the query string while
every other write route reads a JSON body; `/trading/order` requires a stop
that MT5 treats as optional; `/orders/pending` is a POST where `GET /orders/`
is the listing. Each of those, got wrong, produces either a 422 that reads as
a broken bridge or - worse - a well-formed request that does something other
than what was asked.

So the assertions are mostly about the `order_send` request the terminal
received, which is the only place the outcome of all that plumbing is visible.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timezone

import fake_mt5
import pytest

from conftest import last_call

pytestmark = pytest.mark.anyio


async def test_closing_a_position_reaches_the_terminal_at_all(tools, terminal, calls):
    """`POST /positions/close` reads the query string, not a body.

    It declares `ticket`, `volume` and `type_filling` as plain function
    arguments with no request model, so a JSON body is answered
    `422 field required` for `query.ticket` and the position stays open. An
    existing client in this repository sent them as JSON, and the route every
    exit went through had therefore never once worked: a position the client
    opened could not be closed by it.

    A tool with that bug fails here with `ok: false` and an HTTP status of
    422, which is the shape this asserts against.
    """
    terminal.positions = [fake_mt5.position(ticket=77)]

    answer = await tools.close_position(77)

    assert answer["ok"] is True, f"the close did not reach the terminal: {answer}"
    assert answer["retcode"] == 10009
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["position"] == 77


async def test_a_partial_close_sends_the_volume_it_was_given(tools, terminal, calls):
    """A volume dropped on the way turns a partial close into a full exit.

    `volume` rides in the query string with `ticket`. Put it in a body, and
    FastAPI ignores it while still accepting the request, so the route falls
    back to the position's own volume: the agent asked to scale out of half a
    position and is flat instead. Nothing raises, and the only evidence is the
    volume on the request the terminal received.
    """
    terminal.positions = [fake_mt5.position(ticket=77, volume=0.10)]

    answer = await tools.close_position(77, volume=0.04)

    assert answer["ok"] is True
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["volume"] == 0.04, "the terminal was asked to close a different size"


async def test_a_market_order_with_no_stop_is_still_placed(tools, calls):
    """`MarketOrderRequest.sl` has no default, so an omitted stop is a 422.

    It is a bare `float`. MT5 treats a stop as optional and spells "none" as
    `0.0`, but the bridge's model does not: a body without `sl` never reaches
    `order_send` at all. A tool that omitted the field when the agent named no
    stop would be unable to place an unprotected entry - which is the normal
    shape of a bracket, where the protective stop is its own order.
    """
    answer = await tools.place_market_order("XAUUSD", "BUY", 0.05)

    assert answer["ok"] is True, f"the order never reached the terminal: {answer}"
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["sl"] == 0.0


async def test_a_market_order_sends_the_fill_policy_the_symbol_permits(tools, terminal, calls):
    """Filling mode belongs to the symbol, and the wrong one refuses every order.

    The symbol here permits IOC only. An agent asking for FOK - the usual
    default, and a reasonable request - must not have FOK sent on its behalf,
    because the terminal answers 10030 to every order carrying a policy the
    symbol does not list. Not some orders: all of them, on a symbol that is
    otherwise perfectly tradable.

    The bridge resolves this too, and silently. Resolving here as well is what
    makes the substitution visible: `filling_used` and `filling_allowed` tell
    the agent its preference was unreachable rather than leaving it to wonder
    why its FOK orders fill as IOC.
    """
    terminal.symbols["XAUUSD"] = fake_mt5.symbol("XAUUSD", filling_mode=2)

    answer = await tools.place_market_order("XAUUSD", "BUY", 0.05, filling="FOK")

    assert answer["filling_used"] == "IOC"
    assert answer["filling_allowed"] == ["IOC"]
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["type_filling"] == fake_mt5.ORDER_FILLING_IOC


async def test_a_refused_order_comes_back_as_a_reading_not_an_exception(tools, terminal):
    """A refusal has to arrive as a code an agent can branch on.

    The bridge raises 400 with MT5's retcode in the body, which is the right
    shape for an HTTP client and the wrong shape for a model: an exception
    reaches it as prose, and the number that says what to do about it would
    have to be recovered by parsing the sentence.

    10027 is the refusal that has actually cost this stack the most - the
    AutoTrading button in the terminal GUI is off - and it is indistinguishable
    from every other 400 unless the code survives. A tool that let the
    exception through would tell an agent "bad request" about a condition with
    a specific remedy.
    """
    terminal.next_result = fake_mt5.order_result(retcode=10027, comment="Autotrading disabled by client")

    answer = await tools.place_market_order("XAUUSD", "BUY", 0.05)

    assert answer["ok"] is False
    assert answer["retcode"] == 10027
    assert "AutoTrading" in answer["retcode_meaning"]
    assert answer["remedy"]


async def test_a_rejected_order_is_not_reported_as_filled(tools, terminal):
    """`success: true` from the bridge is not evidence that anything happened.

    Six routes once ended `{"success": True, ...}` without reading the
    retcode, so the field named `success` was true on every reply the bridge
    managed to build. `ok` here is computed from the code instead, so a tool
    that trusted the bridge's boolean would report a rejected order as filled
    and an agent would believe it holds a position the account never opened.
    """
    terminal.next_result = fake_mt5.order_result(retcode=10014, comment="Invalid volume")

    answer = await tools.place_market_order("XAUUSD", "BUY", 0.0001)

    assert answer["ok"] is False
    assert answer["retcode"] == 10014
    assert "volume" in answer["retcode_meaning"]


@pytest.mark.parametrize(
    ("order_type", "constant"),
    [
        ("BUY_LIMIT", fake_mt5.ORDER_TYPE_BUY_LIMIT),
        ("SELL_LIMIT", fake_mt5.ORDER_TYPE_SELL_LIMIT),
        ("BUY_STOP", fake_mt5.ORDER_TYPE_BUY_STOP),
        ("SELL_STOP", fake_mt5.ORDER_TYPE_SELL_STOP),
        ("BUY_STOP_LIMIT", fake_mt5.ORDER_TYPE_BUY_STOP_LIMIT),
        ("SELL_STOP_LIMIT", fake_mt5.ORDER_TYPE_SELL_STOP_LIMIT),
    ],
)
async def test_each_resting_order_type_arrives_as_itself(tools, calls, order_type, constant):
    """A resting order must rest, and must rest as the type that was asked for.

    `/trading/order` validates its type against `^(BUY|SELL)$` and its handler
    takes no price at all, so a limit sent there is accepted and **filled at
    the market** - the opposite of what a resting entry is for, with no error
    anywhere. That is why placing a resting order is a separate tool rather
    than a market order with an optional price.

    Every one of the six types the bridge accepts is checked, because the
    mapping from name to MT5 constant is a dictionary and a dictionary with
    one wrong value would place buys where sells were meant on exactly one
    type.
    """
    # The two stop-limit types need both prices: `price` triggers and
    # `limit_price` is where the order then rests. MT5 carries them
    # separately and a request with only the trigger is not the order the
    # caller asked for.
    extra = {"limit_price": 4345.0} if order_type.endswith("STOP_LIMIT") else {}
    answer = await tools.place_pending_order(
        "XAUUSD", order_type, 0.05, price=4350.0, **extra
    )

    assert answer["ok"] is True, f"{order_type} did not reach the terminal: {answer}"
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["action"] == fake_mt5.TRADE_ACTION_PENDING
    assert args[0]["type"] == constant
    assert args[0]["price"] == 4350.0
    if extra:
        assert args[0]["stoplimit"] == 4345.0, "the resting price never reached the terminal"


async def test_a_stop_limit_without_its_resting_price_is_refused(tools, calls):
    """Refused by the tool, before the bridge sees it.

    The alternative is placing an order that is not the one asked for, which
    is what happened while the bridge dropped the field: the type was offered,
    the call succeeded in shape, and the terminal received an incomplete
    request.
    """
    answer = await tools.place_pending_order("XAUUSD", "BUY_STOP_LIMIT", 0.05, price=4350.0)

    assert answer["ok"] is False
    assert "limit_price" in answer["error"]


async def test_a_resting_order_carries_its_stop_target_and_expiry(tools, calls):
    """Optional fields must survive the body, not be dropped as optional.

    `sl`, `tp` and `expiration` are all `Optional` on the bridge's model, so
    omitting one is legal and a tool that mishandled it would place a bare
    order with no error. An entry that rests without its protective stop is
    exactly the position nobody is watching.
    """
    expiry = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)

    answer = await tools.place_pending_order(
        "XAUUSD", "BUY_LIMIT", 0.05, price=4350.0, stop_loss=4340.0, take_profit=4380.0, expiry=expiry
    )

    assert answer["ok"] is True
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["sl"] == 4340.0
    assert args[0]["tp"] == 4380.0
    assert args[0]["type_time"] == fake_mt5.ORDER_TIME_SPECIFIED
    assert args[0]["expiration"] == int(expiry.timestamp())


async def test_moving_a_stop_addresses_the_position_by_ticket(tools, terminal, calls):
    """`POST /positions/modify` takes a body and an MT5 ticket.

    The older route for this, `/trading/modify-sl-tp`, takes a `trade_id` from
    the bridge's own database and so cannot touch a position it did not
    record - which excludes every position opened before a redeploy or placed
    through the terminal directly. A tool pointed at it would work in testing
    and fail on exactly the positions an agent inherits.
    """
    terminal.positions = [fake_mt5.position(ticket=51)]

    answer = await tools.modify_position(51, stop_loss=4398.0, take_profit=4410.0)

    assert answer["ok"] is True
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["action"] == fake_mt5.TRADE_ACTION_SLTP
    assert args[0]["position"] == 51
    assert args[0]["sl"] == 4398.0
    assert args[0]["tp"] == 4410.0


async def test_a_refused_stop_move_is_not_reported_as_moved(tools, terminal):
    """The refusal that costs the most, because it ends the watching.

    `modify_sl_tp` returns `None` when the terminal refuses outright, and the
    route once called `_asdict()` on it - so a refused stop move and a broken
    bridge were the same 500 from outside. An agent told its stop moved stops
    managing the position, and a position nobody is managing is one whose stop
    nobody will move again.
    """
    terminal.positions = [fake_mt5.position(ticket=51)]
    terminal.next_result = fake_mt5.order_result(retcode=10016, comment="Invalid stops")

    answer = await tools.modify_position(51, stop_loss=4400.49)

    assert answer["ok"] is False
    assert answer["retcode"] == 10016
    assert "stops_level" in answer["remedy"]


async def test_moving_a_resting_order_uses_the_order_route(tools, monkeypatch, calls):
    """A resting order is not a position, and the two routes do different things.

    Sent to `/positions/modify`, a resting order's new limit price is posted
    as a take-profit and the order does not move - a 200, a changed account,
    and not the change that was asked for. `PUT /orders/{ticket}` is the route
    for it, and the request it produces carries `TRADE_ACTION_MODIFY` against
    an order rather than `TRADE_ACTION_SLTP` against a position.
    """
    Order = namedtuple("Order", "ticket symbol type sl tp type_time price_open")
    resting = Order(
        ticket=4242,
        symbol="XAUUSD",
        type=fake_mt5.ORDER_TYPE_BUY_LIMIT,
        sl=4340.0,
        tp=4380.0,
        type_time=fake_mt5.ORDER_TIME_GTC,
        price_open=4350.0,
    )
    monkeypatch.setattr(fake_mt5, "orders_get", lambda *a, **k: (resting,))

    answer = await tools.modify_pending_order(4242, price=4345.0, stop_loss=4335.0)

    assert answer["ok"] is True
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["action"] == fake_mt5.TRADE_ACTION_MODIFY
    assert args[0]["order"] == 4242
    assert args[0]["price"] == 4345.0
    assert args[0]["sl"] == 4335.0


async def test_cancelling_a_resting_order_removes_it_by_ticket(tools, calls):
    """A cancel the terminal refused once answered `success: true`.

    The client then reported the order gone while it was still working, and
    was surprised by its fill. The retcode decides here, and the request that
    reaches the terminal must be a removal of that ticket rather than anything
    else addressed to it.
    """
    answer = await tools.cancel_pending_order(4242)

    assert answer["ok"] is True
    _, args, _ = last_call(calls, "order_send")
    assert args[0]["action"] == fake_mt5.TRADE_ACTION_REMOVE
    assert args[0]["order"] == 4242


async def test_close_all_counts_what_closed_and_honours_the_magic_filter(tools, terminal):
    """Flattening one strategy's book must not touch another's positions.

    `close_all` with a magic closes only the positions carrying it. Without
    the filter it closes everything on the account, including trades an agent
    did not open - so a filter that silently did nothing would be the most
    expensive kind of no-op.

    The count is also what *closed*: the bridge logs a refusal on one position
    and continues to the next, so a number lower than the open count means
    some were refused rather than that fewer were open.
    """
    terminal.positions = [
        fake_mt5.position(ticket=1, magic=777_701),
        fake_mt5.position(ticket=2, magic=0),
    ]

    mine = await tools.close_all_positions(magic=777_701)

    assert mine["closed"] == 1
    assert [o["retcode"] for o in mine["outcomes"]] == [10009]


async def test_close_all_reports_every_position_when_no_filter_is_given(tools, terminal):
    """No filter means the whole account, which is the dangerous default.

    Stated as a test so the behaviour is pinned rather than assumed: an agent
    reading the tool description is told this closes everything, and if the
    implementation quietly filtered to its own magic the description would be
    the thing that was wrong.
    """
    terminal.positions = [
        fake_mt5.position(ticket=1, magic=777_701),
        fake_mt5.position(ticket=2, magic=0),
    ]

    everything = await tools.close_all_positions()

    assert everything["closed"] == 2


async def test_an_unknown_side_is_refused_before_it_reaches_the_bridge(tools, calls):
    """`side` is validated against a pattern on the bridge and a 422 says little.

    Caught here instead, with a message naming the two values that work. The
    defect this prevents is an agent retrying a malformed side against a
    validation error that names a pydantic field it has never seen, which it
    cannot act on and will usually respond to by trying a different symbol.
    """
    answer = await tools.place_market_order("XAUUSD", "LONG", 0.05)

    assert answer["ok"] is False
    assert "BUY or SELL" in answer["error"]
    assert not [c for c in calls if c[0] == "order_send"], "a malformed side reached the terminal"
