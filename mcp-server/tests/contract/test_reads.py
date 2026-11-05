"""What the read tools ask the bridge for, and what they hand back.

Every test here is about a mistake that produces a **200 and a wrong answer**.
FastAPI drops a query parameter it does not declare, so a misspelled window or
a misspelled bar count is not an error: the route answers successfully from its
own default, and the caller is told it received what it asked for. The only
place that is visible is in the arguments the terminal was called with, which
is what `calls` reads.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timedelta, timezone

import fake_mt5
import httpx
import pytest

from conftest import last_call
from mt5_mcp.bridge import Bridge, BridgeError
from mt5_mcp.tools import Tools

pytestmark = pytest.mark.anyio


async def test_account_status_reads_autotrading_from_the_terminal_not_the_account(tools, terminal):
    """`autotrading_enabled` must be the terminal's flag, never the account's.

    MT5 has two booleans spelled `trade_allowed`. The account's is the
    broker's permission; the terminal's is the AutoTrading button, and only
    the terminal's decides whether an order fills. With the button off, every
    order is refused with 10027 while the account's flag, the health route and
    the ping all read true - which is how a desk ran for a day believing it
    could trade.

    The fake is set to exactly that state: terminal off, account on. A tool
    reading the account's flag would report `autotrading_enabled: true` and
    `can_place_orders: true`, and an agent would plan entries it cannot place.
    """
    terminal.trade_allowed = False

    status = await tools.account_status()

    assert status["autotrading_enabled"] is False
    assert status["broker_allows_trading"] is True
    assert status["can_place_orders"] is False


async def test_account_status_names_the_currency_its_money_is_in(tools):
    """Balance, equity and margin are meaningless without the currency.

    An agent sizing a position multiplies a money figure by something. If the
    currency is absent from the response it will assume one, and the figure it
    assumes is the instrument's quote currency - which is the account's only
    by coincidence. The currency is required to sit in the same object as the
    numbers, so it cannot be dropped while they survive.
    """
    status = await tools.account_status()
    account = status["account"]

    assert account["currency"] == "USD"
    assert account["balance"] == 10_000.0
    assert account["equity"] == 10_002.5
    assert account["margin_free"] == 9_500.0
    assert account["is_demo"] is True


async def test_instrument_spec_quotes_the_tick_and_never_the_specification(tools, terminal):
    """The frozen bid/ask must not be reachable through this tool.

    `symbol_info` carries a bid/ask that the terminal refreshes on its own
    terms: eight reads three seconds apart returned the same pair and the same
    timestamp while the tick moved on every one. A client that polled it
    published 7 quotes from 2,399 polls, because its de-duplication was
    correctly suppressing a price that never changed.

    Here the specification and the tick are set to different prices. A tool
    that passed `symbol_info` through would answer the specification's 4400.0,
    and a tool that merely renamed the fields would still leave a stale price
    an agent could size off - so the assertion is both that the quote is the
    tick's and that no bid or ask appears anywhere outside the quote block.
    """
    terminal.symbols["XAUUSD"] = fake_mt5.symbol("XAUUSD", bid=4400.0, ask=4400.5)
    terminal.ticks["XAUUSD"] = fake_mt5.tick(bid=4411.0, ask=4411.5)

    spec = await tools.instrument_spec("XAUUSD")

    assert spec["quote"]["bid"] == 4411.0
    assert spec["quote"]["ask"] == 4411.5
    outside_the_quote = {k: v for k, v in spec.items() if k != "quote"}
    assert "bid" not in outside_the_quote
    assert "ask" not in outside_the_quote


async def test_instrument_spec_converts_the_spread_from_points_to_price(tools, terminal):
    """`spread` is in points. Reported as price it is wrong by 1/point.

    The bridge's `spread` column is the terminal's own, in points, and a
    consumer that read it as price once computed a 476% round trip on an FX
    pair - which did not merely overstate the cost, it reordered every
    instrument by how expensive it was to trade.

    Gold here has `point` 0.01 and a 50-point spread, so the price spread is
    0.50. A tool that forwarded the raw column as a price would report 50.0, a
    hundred times the truth, and an agent would reject every trade as too
    expensive.
    """
    terminal.symbols["XAUUSD"] = fake_mt5.symbol("XAUUSD", bid=4400.0, ask=4400.5)

    spec = await tools.instrument_spec("XAUUSD")

    assert spec["point"] == 0.01
    assert spec["spread_points"] == 50
    assert spec["spread_price"] == 0.5


async def test_instrument_spec_decodes_the_filling_bitmask(tools, terminal):
    """`filling_mode` is a bitmask, and sending the wrong policy costs every order.

    1 is FOK, 2 is IOC, 4 is RETURN, and a symbol permitting both FOK and IOC
    reports 3. An agent handed the raw integer has no way to know that 3 is
    two policies rather than a third one, and a policy the symbol does not
    permit is refused with 10030 on every single order - not on some of them.
    """
    terminal.symbols["XAUUSD"] = fake_mt5.symbol("XAUUSD", filling_mode=3)

    spec = await tools.instrument_spec("XAUUSD")

    assert spec["filling_allowed"] == ["FOK", "IOC"]


async def test_instrument_spec_says_so_when_the_symbol_is_unknown(tools):
    """An unknown symbol is an answer, not an exception.

    The bridge returns 404 for a name the terminal does not list, including
    for the right instrument in the wrong case - `eurusd` where the broker
    spells it `EURUSD`. An agent needs that as a result it can act on by
    searching for the right spelling, not as a tool error it can only report.
    """
    spec = await tools.instrument_spec("NOTATHING")

    assert spec["found"] is False
    assert "error" in spec


async def test_bars_asks_for_the_number_of_bars_it_was_given(tools, calls):
    """`/symbols/rates/pos` counts with `num_bars`, and `count` is dropped.

    The route declares `num_bars` with a default of 100. A caller sending
    `count` - the obvious name, and the one an existing client in this
    repository sends - is answered 100 bars with a 200, whatever it asked for.
    Nothing raises, so the only evidence is the count that reached the
    terminal.

    A tool with that bug would pass this suite's higher-level assertions and
    hand an agent 100 bars whenever it asked for any other number.
    """
    await tools.bars("XAUUSD", timeframe="M5", count=7)

    _, args, _ = last_call(calls, "copy_rates_from_pos")
    assert args[3] == 7, "the terminal was asked for a different number of bars than the tool was"


async def test_bars_over_a_range_sends_both_edges_of_the_window(tools, calls):
    """A range answer must be the window asked for, not a count from one end.

    `/symbols/rates/range` spells its edges `start`/`end` - a third spelling,
    after the history routes' `from_date`/`to_date` and the tick range's
    `date_from`/`date_to`. Both are declared without defaults, so the wrong
    name is a 422 rather than a silent substitution; this pins that the right
    names are used and that the edges arrive in order.
    """
    start = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 1, 17, 0, tzinfo=timezone.utc)

    answer = await tools.bars("XAUUSD", timeframe="H1", start=start, end=end)

    _, args, _ = last_call(calls, "copy_rates_range")
    assert args[2].hour == 9 and args[3].hour == 17
    assert answer["window"] == "the requested range"


async def test_bars_refuses_half_a_range_rather_than_guessing_the_other_edge(tools):
    """One edge of a window is ambiguous, and a guess would be invisible.

    Given only a start, a tool could reasonably mean "from then until now" or
    "the most recent N bars ending then" - which is what
    `/symbols/rates/from` does, counting *backward* from its `date_from`. An
    agent that meant one and got the other would read a window from the wrong
    side of its signal with nothing in the response to say so.
    """
    answer = await tools.bars("XAUUSD", start=datetime(2026, 3, 1, tzinfo=timezone.utc))

    assert answer["bars"] == []
    assert "both start and end" in answer["error"]


async def test_trade_history_sends_the_window_it_claims_to_have_used(tools, calls):
    """An omitted window on this bridge is 30 days, substituted silently.

    `/history/deals` spells its range `from_date`/`to_date` while the symbol
    routes spell it `date_from`/`date_to`. FastAPI drops the unknown name and
    `HistoryService` falls back to `now - 30 days`, so asking for one hour
    with the wrong spelling returned 4,013 deals spanning a month, with a 200
    and nothing to indicate the window had been ignored.

    This asks for one hour and reads the date that reached the terminal. The
    wrong spelling would put it 30 days back, and the reported window would
    still say one hour - an agent reconciling its last trade would be handed a
    month of someone else's.
    """
    before = datetime.now(timezone.utc)

    answer = await tools.trade_history(lookback_hours=1)

    _, args, _ = last_call(calls, "history_deals_get")
    asked_from = args[0]
    asked_from = asked_from if asked_from.tzinfo else asked_from.replace(tzinfo=timezone.utc)
    drift = abs((before - timedelta(hours=1)) - asked_from)
    assert drift < timedelta(minutes=1), f"the terminal was asked from {asked_from}, not an hour back"
    assert answer["window_start_utc"] is not None


async def test_trade_history_reads_a_naive_window_edge_as_utc(tools, calls):
    """A naive timestamp must not be read in the bridge process's timezone.

    The bridge's routes type their range parameters as `datetime`, and a naive
    string is parsed in whatever zone that process runs in - the terminal's,
    which is the broker's and is not UTC on any broker this stack has been
    pointed at. A window shifted by the broker's offset comes back as a market
    that was quiet.
    """
    start = datetime(2026, 3, 1, 9, 0)
    end = datetime(2026, 3, 1, 17, 0)

    answer = await tools.trade_history(start=start, end=end)

    assert answer["window_start_utc"].endswith("+00:00")
    _, args, _ = last_call(calls, "history_deals_get")
    assert args[0].utcoffset() == timedelta(0), "the edge reached the bridge without a zone"
    assert args[0].hour == 9, "the hour moved, so the naive edge was read in some other zone"


async def test_open_positions_treats_magic_zero_as_a_filter(tools, terminal):
    """A magic of 0 is a real magic: it is what a hand-placed trade carries.

    `positions_get` does not accept a `magic` keyword at all - the fake
    refuses it exactly as the real package does - so the filter is applied
    over the rows. Written as `if magic`, zero would fall through as "no
    filter" and an agent asking for hand-placed trades would be handed the
    whole account, including the positions another strategy owns.
    """
    terminal.positions = [
        fake_mt5.position(ticket=1, magic=0),
        fake_mt5.position(ticket=2, magic=777_701),
    ]

    hand_placed = await tools.open_positions(magic=0)
    everything = await tools.open_positions()

    assert [p["ticket"] for p in hand_placed["positions"]] == [1]
    assert everything["count"] == 2


async def test_open_positions_decodes_the_side_and_names_the_profit_currency(tools, terminal):
    """A position's `type` is an integer, and 1 does not read as "sell".

    An agent that has to remember MT5's integer encoding to know which way it
    is positioned will eventually get it backwards, and a hedge placed on the
    wrong side doubles the exposure it was meant to remove.
    """
    terminal.positions = [fake_mt5.position(ticket=9, side=fake_mt5.POSITION_TYPE_SELL)]

    answer = await tools.open_positions()
    held = answer["positions"][0]

    assert held["side"] == "SELL"
    assert held["volume_lots"] == 0.05
    assert held["floating_profit"] == 2.5
    assert "account's currency" in answer["note"]


async def test_pending_orders_reads_the_listing_route_and_not_the_placing_one(tools, terminal, monkeypatch):
    """`GET /orders/` lists; `POST /orders/pending` places. There is no GET at the latter.

    A client that asked `/orders/pending` for a list got a 404, and treating
    404 as empty turned that into "no resting orders" - an answer nothing ever
    contradicts, so cancel verification and resting-order reconciliation both
    read it as fact.

    One resting order is planted here. A tool pointed at the placing route
    would return an empty list and this test would see a count of zero.
    """
    Order = namedtuple(
        "Order",
        "ticket symbol type volume_initial volume_current price_open sl tp magic comment "
        "time_setup time_expiration",
    )
    resting = Order(
        ticket=4242,
        symbol="XAUUSD",
        type=fake_mt5.ORDER_TYPE_BUY_LIMIT,
        volume_initial=0.05,
        volume_current=0.05,
        price_open=4350.0,
        sl=4340.0,
        tp=4380.0,
        magic=777_701,
        comment="rest",
        time_setup=1_700_000_000,
        time_expiration=0,
    )
    monkeypatch.setattr(fake_mt5, "orders_get", lambda *a, **k: (resting,))

    answer = await tools.pending_orders()

    assert answer["count"] == 1
    assert answer["orders"][0]["ticket"] == 4242
    assert answer["orders"][0]["type"] == "BUY_LIMIT"


async def test_quote_keeps_the_good_symbols_when_one_is_unknown(tools):
    """One bad name in a batch must not cost the others their quotes.

    The batch route answers 200 with a per-symbol error map precisely so a
    caller does not have to fall back to one request per symbol - which is the
    cost the batch exists to remove. A tool that treated the presence of
    `errors` as a failed call would throw away every quote it was handed.
    """
    answer = await tools.quote(["XAUUSD", "NOTATHING"])

    assert "XAUUSD" in answer["quotes"]
    assert "NOTATHING" in answer["unavailable"]
    assert answer["quotes"]["XAUUSD"]["spread_price"] == pytest.approx(0.5)


async def test_quote_refuses_a_batch_larger_than_the_bridge_accepts(tools):
    """The bridge caps a batch at 40 and answers 422 above it.

    Refused here with a message naming the count, so an agent learns to split
    the request. Passed through, it would come back as a validation error
    about a route the agent never called, and the natural reaction to that is
    to stop asking for quotes.
    """
    answer = await tools.quote([f"SYM{i}" for i in range(41)])

    assert answer["quotes"] == {}
    assert "41" in answer["error"]


async def test_the_credential_travels_in_the_x_api_key_header(app):
    """A wrong key must be a refusal, which proves the right one is being sent.

    The bridge disables authentication entirely when its seed is unset, so a
    suite run against an unauthenticated bridge would pass with no credential
    at all, with the wrong header name, or with the seed in place of its
    SHA-256. The seed is set for these tests, and this asserts the refusal -
    the only evidence that the header the other tests rely on is the one the
    bridge reads.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://bridge", headers={"X-API-Key": "not-the-key"}
    ) as client:
        with pytest.raises(BridgeError) as refused:
            await Tools(Bridge(client)).account_status()

    assert refused.value.status == 401
