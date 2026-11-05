"""The tool surface, shaped around what a trading agent has to decide.

A tool here is not a route. The bridge's routes are the terminal's vocabulary -
`symbol_info`, `copy_rates_from_pos`, `positions_get` - and an agent asking
"what is this costing me" would have to make three calls and know which of two
bid/ask pairs is live. So the questions are the unit instead: *can I trade and
with what*, *what can I trade*, *what does it cost*, *where is the market*,
*what am I holding*, *what happened*. Each answer is assembled from however
many routes it takes.

Two rules the descriptions follow, because an agent cannot see what is not
written down:

* **Every number states its unit.** `spread` on this bridge is in *points*,
  not price; reading it as price once made an FX pair look like a 476% round
  trip and reordered every instrument by cost. Money is in the account's
  currency, which is named in the same object.
* **A frozen value is never presented as a live one.** `symbol_info` carries a
  `bid`/`ask` that the terminal refreshes on its own terms - eight reads three
  seconds apart returned the same pair and the same timestamp - so this layer
  drops those two fields from the specification entirely and the only bid/ask
  an agent can reach comes from the tick routes. Relabelling them would have
  left a field an agent could still size off.

Nothing here imports the MCP SDK, so these handlers can be exercised against
the bridge's own FastAPI application in a process built from the bridge's
requirements, which pin `pydantic<2.0.0` and therefore cannot hold the SDK.
"""

# No `from __future__ import annotations` here, deliberately. The MCP SDK
# builds a tool's input schema by inspecting these signatures and calls
# `issubclass` on each parameter's annotation; under the future import every
# annotation is a string and that call raises, so the import would make every
# tool unregisterable. It needs Python 3.10 or newer for `X | None` to be a
# runtime expression, which `pyproject.toml` requires.
from datetime import datetime, timedelta, timezone
from typing import Any

from .bridge import Bridge, BridgeError
from .retcodes import explain

#: Names of every tool this module provides, in the order they are registered.
#:
#: The registry is a plain list rather than something derived from `dir()` so
#: that adding a method to `Tools` is not the same act as exposing it. A
#: registration test compares the server's listing against this, which is what
#: makes a tool that was written but never registered - or registered twice
#: under different names - a failing test rather than a capability an agent
#: never discovers.
TOOL_NAMES: tuple[str, ...] = (
    "account_status",
    "search_symbols",
    "instrument_spec",
    "quote",
    "bars",
    "open_positions",
    "pending_orders",
    "trade_history",
    "place_market_order",
    "place_pending_order",
    "modify_position",
    "modify_pending_order",
    "close_position",
    "close_all_positions",
    "cancel_pending_order",
)

#: Tools that change the account. Everything else only reads.
#:
#: Used to annotate the registered tools, so a client can tell an agent which
#: calls are reversible. It is a statement about the tool, not a permission:
#: all of them are registered and callable.
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "place_market_order",
        "place_pending_order",
        "modify_position",
        "modify_pending_order",
        "close_position",
        "close_all_positions",
        "cancel_pending_order",
    }
)

#: MT5's `SYMBOL_FILLING_*` bits, which are **not** the `ORDER_FILLING_*`
#: constants an order carries: the mask is FOK=1, IOC=2, RETURN=4 while the
#: order constants are FOK=0, IOC=1, RETURN=2. Reusing one for the other sends
#: IOC where FOK was meant.
FILLING_BITS: dict[str, int] = {"FOK": 1, "IOC": 2, "RETURN": 4}

#: `SYMBOL_TRADE_MODE_*`, which decides whether an order is possible at all.
SYMBOL_TRADE_MODE = {
    0: "disabled",
    1: "long only",
    2: "short only",
    3: "close only",
    4: "full access",
}

#: `ACCOUNT_TRADE_MODE_*`. Keyed on `trade_mode`, not `margin_mode`: this
#: terminal reports `trade_mode: 0` beside `margin_mode: 2`, so reading the
#: wrong one calls a demo account real.
ACCOUNT_TRADE_MODE = {0: "demo", 1: "contest", 2: "real"}

#: `POSITION_TYPE_*` and the pending half of `ORDER_TYPE_*`.
POSITION_SIDE = {0: "BUY", 1: "SELL"}
ORDER_TYPE_NAMES = {
    0: "BUY",
    1: "SELL",
    2: "BUY_LIMIT",
    3: "SELL_LIMIT",
    4: "BUY_STOP",
    5: "SELL_STOP",
    6: "BUY_STOP_LIMIT",
    7: "SELL_STOP_LIMIT",
}

#: The six resting order types `PendingOrderRequest` accepts.
PENDING_TYPES = (
    "BUY_LIMIT",
    "SELL_LIMIT",
    "BUY_STOP",
    "SELL_STOP",
    "BUY_STOP_LIMIT",
    "SELL_STOP_LIMIT",
)

#: MT5 truncates an order comment at 31 characters. Truncating here means the
#: comment an agent reads back from a position is the one it will actually
#: find, rather than a longer string it can never match against.
COMMENT_LIMIT = 31

#: Symbols one `quote` call may ask for. The bridge refuses more than 40 with
#: a 422; matching the limit here turns that into a clear refusal naming the
#: count instead of a validation error from a route the agent did not call.
QUOTE_LIMIT = 40

#: Bars one `bars` call may ask for. Not a limit on history: every bar becomes
#: a dict and then JSON inside a memory-limited container, and the bridge has
#: been measured answering 50,000 and failing at 100,000. Well below that,
#: because the response also has to fit in an agent's context.
BAR_LIMIT = 5000


def allowed_filling(mask: Any) -> list[str]:
    """Which fill policies the symbol permits, decoded from its bitmask.

    An empty list means the terminal reported no mask, which is not the same
    as "none are allowed" and is treated as "it did not say".
    """
    try:
        bits = int(mask or 0)
    except (TypeError, ValueError):
        return []
    return [name for name, bit in FILLING_BITS.items() if bits & bit]


def resolve_filling(requested: str | None, mask: Any) -> tuple[str, list[str]]:
    """The fill policy to send, and the ones the symbol permits.

    **Filling mode is a property of the symbol, not of the account or of the
    caller's preference.** Every symbol on the account this bridge was built
    against reports a mask of 1 - FOK only - and an order carrying IOC is
    refused with `10030` every time. A hardcoded IOC therefore does not break
    some orders; it breaks all of them, on a symbol that is otherwise fine.

    The preference order matches `resolve_filling` in
    `MT5/api/app/services/trade.py`, which applies the same reasoning on the
    bridge's side: honour the request when the symbol allows it, otherwise
    fall back to the first policy that is allowed. Resolving here as well is
    not redundant - the bridge's fallback is silent, so an agent that asked
    for IOC on a FOK-only symbol would be filled FOK and never learn that its
    preference is unreachable. This layer sends the resolved policy and
    reports both it and the permitted set.
    """
    want = (requested or "FOK").upper()
    allowed = allowed_filling(mask)
    if not allowed:
        return want, []
    if want in allowed:
        return want, allowed
    for fallback in ("FOK", "IOC", "RETURN"):
        if fallback in allowed:
            return fallback, allowed
    return want, allowed


def _money(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _utc(when: datetime | None, default: datetime) -> datetime:
    """A window edge, with a naive value read as UTC rather than as local time.

    Stated rather than left to the platform because the bridge's process runs
    in the terminal's timezone, which is the broker's and is not UTC on any
    broker this stack has been pointed at. A window silently shifted by the
    broker's offset comes back as a market that was quiet.
    """
    if when is None:
        return default
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _refusal(exc: BridgeError, what: str) -> dict[str, Any]:
    """A refused write as a result an agent can branch on.

    Returned rather than raised. An exception reaches the model as a string,
    and the number that says *why* - `10019` not enough money, `10016` stops
    too close - would have to be recovered by parsing it. Here the code stays
    an integer beside its meaning, and `ok` is `False`, so the agent's next
    decision is made from fields rather than from prose.
    """
    body = explain(exc.retcode)
    body["ok"] = False
    body["accepted"] = False
    body["what"] = what
    body["error"] = exc.message
    if exc.status is not None:
        body["http_status"] = exc.status
    if exc.mt5_code is not None:
        body["mt5_error_code"] = exc.mt5_code
    return body


def _accepted(raw: dict[str, Any], what: str) -> dict[str, Any]:
    """A write the bridge accepted, with the terminal's retcode made legible.

    The bridge's own `success: true` is not carried through. It is true of
    every reply the bridge manages to build, which is the defect
    `MT5/api/app/utils/sent.py` exists to stop repeating; `ok` here is derived
    from the retcode instead.
    """
    result = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    body = explain(result.get("retcode"))
    body["accepted"] = True
    body["what"] = what
    for field, key in (
        ("order", "order_ticket"),
        ("deal", "deal_ticket"),
        ("volume", "volume_filled"),
        ("price", "fill_price"),
        ("comment", "terminal_comment"),
    ):
        if field in result:
            body[key] = result[field]
    return body


class Tools:
    """The handlers, bound to one bridge connection.

    Methods, not free functions, because every one of them needs the same
    client and the MCP SDK builds a tool's input schema from the signature it
    is handed - a `functools.partial` carrying the client would either leak
    the client into the schema or hide the real parameters from it.
    """

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    async def _filling_for(self, symbol: str, requested):
        """The fill policy to send for one symbol, and the ones it permits.

        A symbol the terminal does not list is answered as "the terminal did
        not say", which leaves the caller's preference untouched. The order
        that follows is then refused by the bridge with a message naming the
        symbol - which is what the agent needs - rather than this lookup
        failing first and reporting a specification error about a call the
        agent never made.
        """
        try:
            spec = await self.bridge.symbol_info(symbol)
        except BridgeError:
            return resolve_filling(requested, 0)
        return resolve_filling(requested, spec.get("filling_mode"))

    # ---------------------------------------------------------------- reads

    async def account_status(self) -> dict[str, Any]:
        """Whether an order can be placed right now, and what there is to size with.

        **`autotrading_enabled` is `terminal_info().trade_allowed`** - the
        AutoTrading button in the MT5 GUI - and it is the flag an order is
        actually checked against. When it is off, every submission is refused
        with `10027` while `broker_allows_trading`, the health route and the
        ping all read green; that combination has hidden a desk that could not
        trade for a full day. `broker_allows_trading` is
        `account_info().trade_allowed`, a different flag set by the broker,
        and being `true` there says nothing about whether an order will fill.
        `can_place_orders` is both of them together with `connected`, and is
        the single field to gate on.

        Money fields - `balance`, `equity`, `margin_used`, `margin_free` - are
        all in `currency`, the account's deposit currency. **That is not
        necessarily the quote currency of the instrument being traded**, so a
        position size computed by dividing equity by a price is wrong for any
        cross whose quote currency differs from `currency`; size from the
        symbol's `tick_value`, which `instrument_spec` reports in this same
        currency.

        `margin_free` is what is left to open with; `margin_level` is equity
        over margin as a percentage and is what a stop-out is measured
        against. `is_demo` comes from `trade_mode`, which is the account's
        demo flag - not `margin_mode`, which this terminal reports as 2 on a
        demo account.
        """
        terminal = await self.bridge.terminal_info()
        account = await self.bridge.account_info()
        connected = bool(terminal.get("connected"))
        autotrading = bool(terminal.get("trade_allowed"))
        mode = account.get("trade_mode")
        return {
            "connected": connected,
            "autotrading_enabled": autotrading,
            "broker_allows_trading": bool(account.get("trade_allowed")),
            "expert_advisors_allowed": bool(account.get("trade_expert")),
            "can_place_orders": connected and autotrading,
            "terminal_build": terminal.get("build"),
            "broker_ping_microseconds": terminal.get("ping_last"),
            "account": {
                "login": account.get("login"),
                "server": account.get("server"),
                "company": account.get("company"),
                "currency": account.get("currency"),
                "account_type": ACCOUNT_TRADE_MODE.get(mode, f"unrecognised trade_mode {mode}"),
                "is_demo": mode == 0,
                "leverage": account.get("leverage"),
                "balance": _money(account.get("balance")),
                "equity": _money(account.get("equity")),
                "floating_profit": _money(account.get("profit")),
                "margin_used": _money(account.get("margin")),
                "margin_free": _money(account.get("margin_free")),
                "margin_level_percent": _number(account.get("margin_level")),
            },
            "note": (
                "autotrading_enabled is the terminal's AutoTrading button and is the flag an "
                "order is checked against; broker_allows_trading is the broker's permission on "
                "the account and is a different flag. All money figures are in the account's "
                "currency, which may differ from the instrument's quote currency."
            ),
        }

    async def search_symbols(self, contains: str = "", limit: int = 100) -> dict[str, Any]:
        """The instrument names this broker offers, narrowed by a substring.

        Names only, because the full list on a retail broker runs to several
        hundred and a specification for each would be most of a context window
        for a question that is really "does this broker call it XAUUSD or
        GOLD". Follow with `instrument_spec` for the one that matters.

        Matching is case-insensitive, but **the names are not**: the bridge
        resolves `EURUSD` and answers 404 for `eurusd`, so pass symbols on to
        other tools exactly as they are spelled here. `total_available` is the
        count before the limit, so a truncated answer says so.
        """
        names = await self.bridge.symbols()
        needle = contains.strip().upper()
        matched = [n for n in names if needle in n.upper()] if needle else list(names)
        capped = max(1, min(int(limit), 500))
        return {
            "symbols": matched[:capped],
            "returned": min(len(matched), capped),
            "total_matching": len(matched),
            "total_available": len(names),
            "note": "symbol names are case-sensitive on every other tool; pass them through unchanged",
        }

    async def instrument_spec(self, symbol: str) -> dict[str, Any]:
        """What one instrument costs to trade and how it must be sized.

        Answers the sizing question in one call: the contract, the live quote
        and the current cost of crossing the spread.

        **Units, each of which has been read as the wrong one at least once:**

        * `spread_points` is in **points, not price**. A point is `point`
          units of price, so the spread in price is
          `spread_points * point` - which this tool computes as
          `spread_price` rather than leaving to the caller, because reading
          the raw column as price once made an FX pair read a 476% round trip
          and reordered every instrument by cost.
        * `tick_value` is money per `tick_size` of price movement per **one
          lot**, in the *account's* currency. It is the term that makes a
          stop distance into a loss figure without any currency conversion of
          the caller's own.
        * `contract_size` is units of the base instrument per lot.
        * `volume_min`, `volume_max`, `volume_step` are in lots. A volume that
          is not a whole multiple of `volume_step` is refused with `10014`.
        * `stops_level_points` is the minimum distance in points between the
          market and a stop or target. Closer than that is refused with
          `10016`.

        **The quote in this answer is live**, read from the tick route. The
        bid and ask carried by MT5's symbol specification are deliberately not
        included: that pair is a snapshot the terminal refreshes on its own
        terms, and eight reads of it three seconds apart returned the same
        bid, the same ask and the same timestamp while the tick moved on every
        one. An agent sizing off the specification would be pricing from a
        startup snapshot with nothing in the response to say so.

        `filling_allowed` is decoded from the symbol's own bitmask and is what
        the order tools send. `tradable` is `trade_mode`: a symbol can be
        quoting and still be close-only.
        """
        try:
            spec = await self.bridge.symbol_info(symbol)
        except BridgeError as exc:
            return {
                "symbol": symbol,
                "found": False,
                "error": exc.message,
                "note": "names are case-sensitive; search_symbols lists them in the broker's own spelling",
            }
        try:
            tick = await self.bridge.tick(symbol)
        except BridgeError as exc:
            # A symbol can be listed and not quoting - an instrument outside
            # its session, or one the terminal has not pulled into Market
            # Watch. The contract is still the answer to a sizing question, so
            # it is returned with the quote marked absent rather than the whole
            # call failing over a price the agent may not have needed.
            tick = {"unavailable": exc.message}
        point = _number(spec.get("point")) or 0.0
        spread_points = _number(spec.get("spread"))
        bid, ask = _number(tick.get("bid")), _number(tick.get("ask"))
        live_spread = round(ask - bid, 10) if bid is not None and ask is not None else None
        mode = spec.get("trade_mode")
        return {
            "symbol": spec.get("name", symbol),
            "found": True,
            "description": spec.get("description"),
            "digits": spec.get("digits"),
            "point": point,
            "contract_size": _number(spec.get("trade_contract_size")),
            "tick_size": _number(spec.get("trade_tick_size")),
            "tick_value": _number(spec.get("trade_tick_value")),
            "volume_min": _number(spec.get("volume_min")),
            "volume_max": _number(spec.get("volume_max")),
            "volume_step": _number(spec.get("volume_step")),
            "stops_level_points": spec.get("trade_stops_level"),
            "spread_points": spread_points,
            "spread_price": round(spread_points * point, 10) if spread_points is not None else None,
            "filling_allowed": allowed_filling(spec.get("filling_mode")),
            "tradable": SYMBOL_TRADE_MODE.get(mode, f"unrecognised trade_mode {mode}"),
            "quote": {
                "bid": bid,
                "ask": ask,
                "spread_price": live_spread,
                "spread_points": round(live_spread / point, 2) if live_spread is not None and point else None,
                "time": tick.get("time"),
                "source": "symbol_info_tick, the latest tick",
            },
            "note": (
                "spread_points is in points; multiply by point for price. tick_value is in the "
                "account's currency per tick_size per lot. The quote block is the live tick; the "
                "specification's own bid/ask is frozen and is not reported."
            ),
        }

    async def quote(self, symbols: list[str]) -> dict[str, Any]:
        """Live bid/ask for up to 40 instruments in one call.

        The price to act on. Each entry carries `spread_price`, the cost of
        crossing in price terms right now - which is the live figure, where
        `instrument_spec`'s `spread_points` is the specification's.

        One unknown symbol does not cost the others their quotes: it lands in
        `unavailable` with the terminal's reason and the rest still answer.
        `time` is the tick's timestamp in the broker's server time, which is
        **not UTC on any broker** - use it to tell two ticks apart, not to
        align against an external clock.

        A quote repeated between calls is a market that did not move, not a
        stale read: the bridge's one-second cache is bypassed for this tool.
        """
        wanted = [s.strip() for s in symbols if s and s.strip()]
        if not wanted:
            return {"quotes": {}, "unavailable": {}, "error": "name at least one symbol"}
        if len(wanted) > QUOTE_LIMIT:
            return {
                "quotes": {},
                "unavailable": {},
                "error": f"asked for {len(wanted)} symbols; the bridge's limit is {QUOTE_LIMIT} per call",
            }
        raw = await self.bridge.ticks(wanted)
        quotes: dict[str, Any] = {}
        for name, tick in (raw.get("ticks") or {}).items():
            if not isinstance(tick, dict):
                continue
            bid, ask = _number(tick.get("bid")), _number(tick.get("ask"))
            quotes[name] = {
                "bid": bid,
                "ask": ask,
                "spread_price": round(ask - bid, 10) if bid is not None and ask is not None else None,
                "time": tick.get("time"),
            }
        return {
            "quotes": quotes,
            "unavailable": raw.get("errors") or {},
            "requested": len(wanted),
            "note": "bid/ask are live ticks; time is broker server time, which is not UTC",
        }

    async def bars(
        self,
        symbol: str,
        timeframe: str = "M15",
        count: int = 200,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        """OHLC history for one instrument.

        Two shapes in one tool. With no `start`/`end` it answers the most
        recent `count` bars, newest last. With both, it answers every bar in
        that window and `count` is ignored - which is the shape to prefer when
        the window matters, because it is stated rather than counted from one
        end.

        **The last bar of a recent-bars answer is still forming**: its close
        is simply the current price, and a pattern read from it is a claim the
        next tick can withdraw. A window that ends in the past has no such
        bar.

        `timeframe` is MT5's own vocabulary: M1 M2 M3 M4 M5 M6 M10 M12 M15 M20
        M30 H1 H2 H3 H4 H6 H8 H12 D1 W1 MN1.

        `time` on each bar is an **ISO 8601 string in UTC**, not an epoch -
        the bridge runs these rows through pandas before serialising them,
        unlike its tick push which carries integers. `tick_volume` is the
        number of price changes in the bar, not traded size; `real_volume` is
        traded size where the broker reports it and 0 where it does not.
        `spread` on a bar is in points, like everywhere else.

        An empty `bars` list with no error is a real answer about a window
        with no trading in it, such as a weekend.
        """
        capped = max(1, min(int(count), BAR_LIMIT))
        if start is not None and end is not None:
            rows = await self.bridge.bars_range(symbol, timeframe, _utc(start, start), _utc(end, end))
            window = "the requested range"
        elif start is not None or end is not None:
            return {
                "symbol": symbol,
                "bars": [],
                "error": "a range needs both start and end; give neither for the most recent bars",
            }
        else:
            rows = await self.bridge.bars_recent(symbol, timeframe, capped)
            window = f"the most recent {capped} bars"
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "window": window,
            "bar_count": len(rows),
            "bars": rows,
            "note": (
                "time is an ISO 8601 UTC string; spread is in points; tick_volume counts price "
                "changes, not size. On a most-recent-bars answer the final bar is still forming."
            ),
        }

    async def open_positions(self, magic: int | None = None, symbol: str | None = None) -> dict[str, Any]:
        """What the account is holding right now.

        `magic` filters to positions carrying that magic number, which is how
        an agent finds its own trades and leaves the rest of the account
        alone. **A magic of `0` is a real value** - it is what a hand-placed
        trade carries - and filtering for it returns hand-placed trades, not
        everything. Omit the argument for no filter.

        `profit` and `swap` are in the account's currency and are floating,
        not realised. `volume` is in lots. `side` is decoded from MT5's
        integer position type. A position that was stopped out produces no
        message anywhere: it simply stops appearing here.
        """
        rows = await self.bridge.positions(magic=magic)
        if symbol:
            rows = [r for r in rows if str(r.get("symbol", "")).upper() == symbol.upper()]
        positions = [
            {
                "ticket": r.get("ticket"),
                "symbol": r.get("symbol"),
                "side": POSITION_SIDE.get(r.get("type"), f"unrecognised type {r.get('type')}"),
                "volume_lots": _number(r.get("volume")),
                "open_price": _number(r.get("price_open")),
                "current_price": _number(r.get("price_current")),
                "stop_loss": _number(r.get("sl")),
                "take_profit": _number(r.get("tp")),
                "floating_profit": _money(r.get("profit")),
                "swap": _money(r.get("swap")),
                "magic": r.get("magic"),
                "comment": r.get("comment"),
                "opened_at_epoch": r.get("time"),
            }
            for r in rows
        ]
        return {
            "positions": positions,
            "count": len(positions),
            "total_floating_profit": _money(sum(p["floating_profit"] or 0.0 for p in positions)),
            "note": (
                "floating_profit and swap are in the account's currency and are unrealised; "
                "volume_lots is in lots. magic=0 is a real magic, not 'no filter'."
            ),
        }

    async def pending_orders(self, symbol: str | None = None) -> dict[str, Any]:
        """Resting orders that have not triggered.

        Separate from `open_positions`: a resting order is not a position, and
        the tools that modify them are different calls. An empty list here is
        a real answer and not a failure - which is worth stating because the
        route that *looks* like it lists resting orders, `/orders/pending`, is
        a POST that places one, and asking it for a list returns a 404 that
        reads as "nothing resting".

        `price` is the trigger price in the instrument's own price units.
        `volume_lots` is in lots. `expires_at_epoch` is 0 for a good-till-
        cancelled order.
        """
        rows = await self.bridge.pending_orders(symbol=symbol)
        orders = [
            {
                "ticket": r.get("ticket"),
                "symbol": r.get("symbol"),
                "type": ORDER_TYPE_NAMES.get(r.get("type"), f"unrecognised type {r.get('type')}"),
                "volume_lots": _number(r.get("volume_current") or r.get("volume_initial")),
                "price": _number(r.get("price_open")),
                "stop_loss": _number(r.get("sl")),
                "take_profit": _number(r.get("tp")),
                "magic": r.get("magic"),
                "comment": r.get("comment"),
                "placed_at_epoch": r.get("time_setup"),
                "expires_at_epoch": r.get("time_expiration"),
            }
            for r in rows
        ]
        return {"orders": orders, "count": len(orders)}

    async def trade_history(
        self,
        lookback_hours: float = 24.0,
        start: datetime | None = None,
        end: datetime | None = None,
        include_orders: bool = False,
        position: int | None = None,
    ) -> dict[str, Any]:
        """What happened: fills, and optionally the orders behind them.

        The window is always sent explicitly. **An omitted window on the
        bridge is 30 days, not all history**, substituted silently when the
        range parameters are dropped - asking for one hour with the wrong
        parameter spelling returned 4,013 deals spanning a month with a 200
        and nothing to say the window had been ignored. So this tool has no
        unbounded mode: `lookback_hours` back from now unless `start` and
        `end` are both given, and the window it used is echoed back.

        A naive `start` or `end` is read as **UTC**, not as the broker's or
        this machine's local time.

        `deals` are fills and are the record of what actually happened to the
        money; `entry` distinguishes a deal that opened a position (`0`) from
        one that closed it (`1`). `profit`, `commission` and `swap` are in the
        account's currency and are realised. Set `include_orders` for the
        order records behind them, which carry the retcode-adjacent `state`
        and are how a rejected order is distinguished from one that never
        existed. `position` narrows deals to one position's ticket, which is
        how an agent reconciles a single trade end to end.

        Keep windows bounded for a second reason: the 30-day payload is about
        a megabyte and has come back unparseable at least once.
        """
        now = datetime.now(timezone.utc)
        if start is not None and end is not None:
            begin, finish = _utc(start, now), _utc(end, now)
        else:
            hours = max(0.01, float(lookback_hours))
            begin, finish = now - timedelta(hours=hours), now
        deals = await self.bridge.deals(begin, finish, position=position)
        answer: dict[str, Any] = {
            "window_start_utc": begin.isoformat(),
            "window_end_utc": finish.isoformat(),
            "deals": deals,
            "deal_count": len(deals),
            "note": (
                "deals are fills. profit, commission and swap are in the account's currency and "
                "are realised. entry 0 opened a position, 1 closed one. The window is always sent "
                "explicitly because an omitted one would silently become 30 days."
            ),
        }
        if include_orders:
            orders = await self.bridge.history_orders(begin, finish)
            answer["orders"] = orders
            answer["order_count"] = len(orders)
        return answer

    # --------------------------------------------------------------- writes

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss: float = 0.0,
        take_profit: float | None = None,
        deviation_points: int = 20,
        magic: int = 0,
        comment: str = "",
        filling: str | None = None,
    ) -> dict[str, Any]:
        """Buy or sell at the market, now.

        `side` is `BUY` or `SELL`. `volume` is in **lots** and must be a whole
        multiple of the symbol's `volume_step`, inside `volume_min`..
        `volume_max`; anything else is refused with `10014`.

        `stop_loss` and `take_profit` are **prices**, not distances. `0.0`
        means no stop, which is how MT5 spells it, and is the default. Both
        must be at least the symbol's `stops_level_points` away from the
        market or the order is refused with `10016`.

        `deviation_points` is the maximum slippage in **points** that is still
        acceptable; a market that moves further while the order is in flight
        comes back `10004` requote rather than filling at a price that was
        never agreed.

        `magic` tags the position so `open_positions(magic=...)` can find it
        again. `comment` is truncated to 31 characters because MT5 truncates
        it anyway, and a comment the agent cannot match on later is worse than
        a short one.

        **The fill policy is taken from the symbol, not from `filling`.** The
        symbol's own bitmask decides which policies exist for it; `filling`
        is a preference, honoured when the symbol permits it and replaced when
        it does not. The answer reports `filling_used` and `filling_allowed`,
        so a preference that could never have worked is visible instead of
        silently downgraded.

        The result carries MT5's `retcode` with a plain meaning and a remedy
        beside it. `ok` is computed from that code: a refusal is `ok: false`
        with the reason, not an exception.
        """
        side = (side or "").strip().upper()
        if side not in ("BUY", "SELL"):
            return {"ok": False, "accepted": False, "error": "side must be BUY or SELL"}
        used, permitted = await self._filling_for(symbol, filling)
        body: dict[str, Any] = {
            "symbol": symbol,
            "volume": float(volume),
            "order_type": side,
            # Sent even when it is zero. `MarketOrderRequest.sl` is a bare
            # `float` with no default, so a body that omits it is a 422 and
            # the order is never placed at all.
            "sl": float(stop_loss or 0.0),
            "deviation": int(deviation_points),
            "magic": int(magic),
            "comment": (comment or "")[:COMMENT_LIMIT],
            "type_filling": used,
        }
        if take_profit:
            body["tp"] = float(take_profit)
        what = f"market {side} {volume} {symbol}"
        try:
            raw = await self.bridge.market_order(body)
        except BridgeError as exc:
            answer = _refusal(exc, what)
        else:
            answer = _accepted(raw, what)
        answer["filling_used"] = used
        answer["filling_allowed"] = permitted
        return answer

    async def place_pending_order(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        expiry: datetime | None = None,
        magic: int = 0,
        comment: str = "",
        filling: str | None = None,
    ) -> dict[str, Any]:
        """Place a resting order that triggers at a price.

        `order_type` is one of `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`,
        `SELL_STOP`, `BUY_STOP_LIMIT`, `SELL_STOP_LIMIT`. A limit rests
        *better* than the market and a stop rests *worse*; a price on the
        wrong side of the market for the type is refused with `10015`.

        **A separate tool from `place_market_order` on purpose.** The bridge's
        market route validates `order_type` against `^(BUY|SELL)$` and its
        handler takes no price at all, so a limit sent there is accepted and
        filled at the market - the opposite of what a resting entry is for,
        and silent. One tool with an optional price would make that mistake
        expressible.

        `price` is the trigger price in the instrument's price units.
        `stop_loss` and `take_profit` are prices, not distances, and may be
        omitted. `volume` is in lots, subject to the symbol's step and bounds.

        `expiry` sets the order to expire at that instant; a naive value is
        read as UTC. Omit it for good-till-cancelled.

        Fill policy comes from the symbol's bitmask, as for a market order.

        **One limitation of the bridge, not of MT5:** for the two stop-limit
        types the bridge sends only `price` and never MT5's separate
        `stoplimit` field, so the resting limit price cannot be expressed
        through this route. A real terminal is expected to refuse those two
        types; the four plain limit and stop types are unaffected.
        """
        kind = (order_type or "").strip().upper()
        if kind not in PENDING_TYPES:
            return {
                "ok": False,
                "accepted": False,
                "error": f"order_type must be one of {', '.join(PENDING_TYPES)}",
            }
        used, permitted = await self._filling_for(symbol, filling)
        body: dict[str, Any] = {
            "symbol": symbol,
            "volume": float(volume),
            "order_type": kind,
            "price": float(price),
            "magic": int(magic),
            "comment": (comment or "")[:COMMENT_LIMIT],
            "type_filling": used,
        }
        if stop_loss is not None:
            body["sl"] = float(stop_loss)
        if take_profit is not None:
            body["tp"] = float(take_profit)
        if expiry is not None:
            body["type_time"] = "SPECIFIED"
            body["expiration"] = _utc(expiry, expiry).isoformat()
        what = f"{kind} {volume} {symbol} at {price}"
        try:
            raw = await self.bridge.pending_order(body)
        except BridgeError as exc:
            answer = _refusal(exc, what)
        else:
            answer = _accepted(raw, what)
        answer["filling_used"] = used
        answer["filling_allowed"] = permitted
        return answer

    async def modify_position(
        self, ticket: int, stop_loss: float, take_profit: float | None = None
    ) -> dict[str, Any]:
        """Move an open position's stop loss and take profit.

        Addressed by the position's MT5 `ticket`, which `open_positions`
        reports. Both values are **prices**, not distances, and both must
        respect the symbol's `stops_level_points` or the change is refused
        with `10016`.

        `stop_loss` is required because that is the shape of the bridge's
        model; pass the position's existing stop to change only the target.
        Omitting `take_profit` leaves the existing one in place rather than
        clearing it - the bridge reads the position's current value and sends
        it back unchanged. To clear either, send `0.0`, which is how MT5
        spells "none".

        A refusal here is the expensive kind: an agent told its stop moved
        stops watching the position. The retcode is checked, so `ok: false`
        means the terminal did not accept it.
        """
        body: dict[str, Any] = {"ticket": int(ticket), "sl": float(stop_loss)}
        if take_profit is not None:
            body["tp"] = float(take_profit)
        what = f"stop/target move on position {ticket}"
        try:
            return _accepted(await self.bridge.modify_position(body), what)
        except BridgeError as exc:
            return _refusal(exc, what)

    async def modify_pending_order(
        self,
        ticket: int,
        price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        expiry: datetime | None = None,
    ) -> dict[str, Any]:
        """Move a resting order's trigger price, stop, target or expiry.

        A resting order is not a position: sending one to `modify_position`
        posts its limit price as a take-profit and leaves the order where it
        was.

        `price` is required - that is the bridge's model, so moving only the
        stop of a resting order is not expressible; pass the order's current
        price to leave it where it is. Omitted `stop_loss` and `take_profit`
        keep the order's existing values rather than clearing them. `expiry`
        sets `SPECIFIED` expiration; a naive value is read as UTC.

        `10025` back from this means the values asked for are the ones the
        order already has.
        """
        body: dict[str, Any] = {"price": float(price)}
        if stop_loss is not None:
            body["sl"] = float(stop_loss)
        if take_profit is not None:
            body["tp"] = float(take_profit)
        if expiry is not None:
            body["type_time"] = "SPECIFIED"
            body["expiration"] = _utc(expiry, expiry).isoformat()
        what = f"modify of resting order {ticket}"
        try:
            return _accepted(await self.bridge.modify_pending(ticket, body), what)
        except BridgeError as exc:
            return _refusal(exc, what)

    async def close_position(
        self, ticket: int, volume: float | None = None, filling: str | None = None
    ) -> dict[str, Any]:
        """Close an open position, in full or in part.

        Omit `volume` to close the whole position. Give a `volume` smaller
        than the position's for a **partial** close: the remainder stays open
        under the same ticket. A volume larger than the position holds is
        refused with `10038`, and one that is not a multiple of the symbol's
        `volume_step` with `10014`.

        Fill policy is resolved from the symbol's own bitmask, as for an entry.
        A close refused for an unsupported filling mode is the worse half of
        that bug: the account stays exposed and the agent was told no.
        """
        what = f"close of position {ticket}" + (f" for {volume} lots" if volume is not None else " in full")
        used: str | None = None
        permitted: list[str] = []
        try:
            positions = await self.bridge.positions()
            holding = next((p for p in positions if p.get("ticket") == int(ticket)), None)
            if holding is not None:
                used, permitted = await self._filling_for(str(holding.get("symbol")), filling)
            raw = await self.bridge.close_position(ticket, volume=volume, type_filling=used)
            answer = _accepted(raw, what)
        except BridgeError as exc:
            answer = _refusal(exc, what)
        if used:
            answer["filling_used"] = used
            answer["filling_allowed"] = permitted
        return answer

    async def close_all_positions(
        self, side: str = "all", magic: int | None = None, filling: str | None = None
    ) -> dict[str, Any]:
        """Close every open position, or every one on a side, or every one with a magic.

        `side` is `all`, `BUY` or `SELL`. `magic` narrows it to the positions
        carrying that magic number, which is how an agent flattens its own
        book without touching positions it did not open; a magic of `0` is a
        real value and selects hand-placed trades.

        **`closed` counts what actually closed, not what was attempted.** The
        bridge logs a refusal on one position and carries on to the next, so a
        count lower than the number of open positions means some were refused
        - compare against `open_positions` afterwards rather than assuming the
        account is flat.
        """
        what = f"close_all side={side}" + (f" magic={magic}" if magic is not None else "")
        try:
            raw = await self.bridge.close_all_positions(order_type=side, magic=magic, type_filling=filling)
        except BridgeError as exc:
            return _refusal(exc, what)
        results = raw.get("results") if isinstance(raw.get("results"), list) else []
        closed = [explain(r.get("retcode")) for r in results if isinstance(r, dict)]
        return {
            "ok": True,
            "accepted": True,
            "what": what,
            "closed": len(results),
            "outcomes": closed,
            "message": raw.get("message"),
            "note": "closed counts positions that closed; a refusal is logged and skipped, so re-read open_positions",
        }

    async def cancel_pending_order(self, ticket: int) -> dict[str, Any]:
        """Remove a resting order that has not triggered.

        `10023` back from this means the order changed state between being
        read and being cancelled - most often it filled, in which case there
        is now a position where the agent expected nothing. Re-read
        `open_positions` on that code rather than treating it as a no-op.
        """
        what = f"cancel of resting order {ticket}"
        try:
            return _accepted(await self.bridge.cancel_order(ticket), what)
        except BridgeError as exc:
            return _refusal(exc, what)
