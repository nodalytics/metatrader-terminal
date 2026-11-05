"""The HTTP client for the MetaTrader 5 bridge in `MT5/api`.

One method per route, and the method is where the route's shape is recorded.
Three of the bridge's conventions are not guessable from the route name, and
all three fail *quietly* when they are guessed wrong, which is why they are
pinned here and tested against the real FastAPI app rather than described in a
comment:

* **`POST /positions/close` and `POST /positions/close_all` take query
  parameters.** They declare `ticket`, `volume` and `type_filling` as plain
  function arguments with no request model, so FastAPI reads them from the
  query string and a JSON body is answered `422 field required` for
  `query.ticket`. Every other write route on this bridge takes a body.
* **`/history/*` spells the window `from_date`/`to_date`** while
  `/symbols/ticks/{s}/range` spells it `date_from`/`date_to` and
  `/symbols/rates/range` spells it `start`/`end`. FastAPI drops a query
  parameter it does not declare, and `HistoryService` then substitutes
  `now - 30 days`, so the wrong spelling returns a month with a 200 and no
  indication the asked-for window was ignored.
* **`/symbols/rates/pos` counts bars with `num_bars`,** not `count`, and
  `num_bars` has a default of 100. A caller sending `count` is silently given
  100 bars however many it asked for.

Nothing here imports the MCP SDK. The tool layer above it does, and keeping
that dependency out means this client - the part with the route contracts in
it - can be exercised against the bridge's own FastAPI application in a
process where the SDK is not installed. The bridge pins `pydantic<2.0.0` and
the MCP SDK requires pydantic 2, so that is not a stylistic preference.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

#: The bridge mounts every protected router under this prefix. `/health` and
#: `/feed` are the two routes outside it and neither is used here.
API = "/api/v1"

#: Seconds before a bridge call is abandoned.
#:
#: Generous because the bridge's calls into the terminal are synchronous IPC
#: and a wide `bars` window is measured in seconds, not milliseconds. A timeout
#: tight enough to feel responsive would turn a large but legitimate history
#: request into an error that looks like an outage.
TIMEOUT = 30.0


class BridgeError(RuntimeError):
    """A request the bridge refused, with whatever it said about why.

    `retcode` is MT5's trade retcode when the failure came from `order_send`;
    the bridge puts it in the body's `code` field. It is carried separately
    from the message because a caller matching on text instead of on a number
    is a caller that breaks when the wording improves.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        retcode: int | None = None,
        mt5_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.retcode = retcode
        self.mt5_code = mt5_code


def endpoint_from_env(environ: dict[str, str] | None = None) -> tuple[str, str]:
    """`MT5_API_URL` and `MT5_API_KEY`, or an error naming the one that is missing.

    `MT5_API_URL` has no default on purpose. A default of `localhost:8000`
    would be a hardcoded endpoint that is wrong everywhere except one machine,
    and wrong in the way that is hardest to see: the server would start, every
    tool would be listed, and every call would fail with a connection error
    that names nothing about configuration.

    `MT5_API_KEY` may be empty, because the bridge disables authentication
    entirely when its own `API_KEY_SEED` is unset and demanding a key would
    make that configuration unusable. It is the *derived* key -
    `sha256(API_KEY_SEED)`, what `POST /api/v1/auth/login` hands back - and not
    the seed. Sending the seed produces `401 Invalid or missing API Key` on
    every protected route, which reads like a wrong key rather than a key that
    was never hashed.
    """
    env = os.environ if environ is None else environ
    url = (env.get("MT5_API_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "MT5_API_URL is not set. It is the base URL of the MetaTrader 5 bridge, "
            "for example the address this process reaches the terminal's FastAPI "
            "service on. There is no default."
        )
    return url.rstrip("/"), (env.get("MT5_API_KEY") or "").strip()


def iso(when: datetime) -> str:
    """A datetime as an explicitly-UTC ISO 8601 string.

    Every range parameter on the bridge is typed `datetime`, and pydantic
    reads a naive string in the bridge process's own timezone - which is the
    terminal's, and is not UTC on any broker this stack has been pointed at. A
    window handed over naive is therefore silently shifted by the broker's
    offset, and a shifted window reads as a market that was quiet.
    """
    stamped = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return stamped.astimezone(timezone.utc).isoformat()


class Bridge:
    """Routes, as the bridge exposes them. Returns parsed JSON and nothing else.

    Constructed either from the environment - `Bridge.from_env()` - or around
    a caller-supplied `httpx.AsyncClient`, which is how the contract tests
    point it at the bridge's FastAPI application in-process instead of at a
    socket.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> Bridge:
        url, key = endpoint_from_env()
        headers = {"X-API-Key": key} if key else {}
        return cls(httpx.AsyncClient(base_url=url, headers=headers, timeout=TIMEOUT))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- wire

    async def _send(self, method: str, route: str, **kwargs: Any) -> Any:
        params = kwargs.get("params")
        if params is not None:
            kwargs["params"] = {k: v for k, v in params.items() if v is not None}
        try:
            response = await self._client.request(method, f"{API}{route}", **kwargs)
        except httpx.HTTPError as exc:
            raise BridgeError(f"{method} {route} could not reach the bridge: {exc}") from exc
        if response.status_code >= 400:
            raise BridgeError(**_failure(method, route, response))
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise BridgeError(f"{method} {route} returned a body that is not JSON") from exc

    async def get(self, route: str, **params: Any) -> Any:
        return await self._send("GET", route, params=params)

    async def post_body(self, route: str, body: dict[str, Any]) -> Any:
        return await self._send("POST", route, json=body)

    async def post_query(self, route: str, **params: Any) -> Any:
        return await self._send("POST", route, params=params)

    async def put_body(self, route: str, body: dict[str, Any]) -> Any:
        return await self._send("PUT", route, json=body)

    async def delete(self, route: str) -> Any:
        return await self._send("DELETE", route)

    # --------------------------------------------------------------- reads

    async def terminal_info(self) -> dict[str, Any]:
        """The terminal's own state, which is **where the AutoTrading flag lives**.

        `terminal_info().trade_allowed` and `account_info().trade_allowed` are
        different facts spelled the same way. This one is the AutoTrading
        button in the MT5 GUI and it is the one an order is checked against;
        with it off every submission comes back `10027` while the account's
        own flag, `/account/health` and `/terminal/ping` all read green.
        """
        return _as_dict(await self.get("/terminal/info"))

    async def account_info(self) -> dict[str, Any]:
        """Balance, equity, margin, currency and leverage.

        `trade_mode` is the demo check - 0 demo, 1 contest, 2 real - and not
        `margin_mode`, which this terminal reports as 2 beside a `trade_mode`
        of 0.
        """
        return _as_dict(await self.get("/terminal/account/info"))

    async def symbols(self) -> list[str]:
        raw = await self.get("/symbols/")
        return [str(s) for s in (raw or [])]

    async def symbol_info(self, symbol: str) -> dict[str, Any]:
        """The symbol's contract. **Its `bid`/`ask` is not a quote.**

        `symbol_info` returns the specification, and the bid/ask on it is a
        snapshot the terminal refreshes on its own terms: eight reads three
        seconds apart returned the same bid, the same ask and the same `time`
        while `tick()` moved on every one. The tool layer drops those two
        fields rather than relabelling them.

        Case-sensitive, in the broker's own spelling: `EURUSD` resolves where
        `eurusd` is a 404.
        """
        return _as_dict(await self.get(f"/symbols/info/{symbol}"))

    async def tick(self, symbol: str) -> dict[str, Any]:
        """The latest tick for one symbol - the live quote."""
        return _as_dict(await self.get(f"/symbols/ticks/{symbol}"))

    async def ticks(self, symbols: list[str], use_cache: bool = False) -> dict[str, Any]:
        """Live quotes for many symbols in one request.

        Partial success is the route's design: `ticks` holds what resolved and
        `errors` holds a message per symbol that did not, with a 200 even when
        nothing resolved. One unknown symbol therefore does not cost the other
        twenty-nine their quotes.

        `use_cache=False` because the bridge's cache holds a tick for one
        second and an agent sampling faster than that would be handed the same
        quote twice and told it was two.
        """
        return _as_dict(
            await self.get("/symbols/ticks", symbols=",".join(symbols), use_cache=use_cache)
        )

    async def bars_recent(self, symbol: str, timeframe: str, num_bars: int) -> list[dict[str, Any]]:
        """The most recent bars, newest last. **`num_bars`, not `count`.**

        `/symbols/rates/pos` declares `num_bars` with a default of 100, so a
        caller sending `count` is given 100 bars and no error - the request is
        well formed, the parameter is simply dropped.
        """
        return _as_rows(await self.get("/symbols/rates/pos", symbol=symbol, timeframe=timeframe, num_bars=num_bars))

    async def bars_range(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Every bar between two instants. **`start`/`end`, a third spelling.**

        Unlike the history routes, both are declared without defaults, so a
        wrong name here is a 422 rather than a silently substituted window.
        That is the better failure and it is the reason this route is
        preferred over `/symbols/rates/from`, whose `date_from` is the
        *newest* bar of the sample and counts backward from it.
        """
        return _as_rows(
            await self.get(
                "/symbols/rates/range", symbol=symbol, timeframe=timeframe, start=iso(start), end=iso(end)
            )
        )

    async def positions(self, magic: int | None = None) -> list[dict[str, Any]]:
        """Open positions, optionally only those carrying `magic`.

        A magic of `0` is a real value - it is what a hand-placed trade
        carries - and the bridge filters for it rather than treating it as
        "no filter".
        """
        return _as_rows(await self.get("/positions/", magic=magic))

    async def pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Resting orders. **`GET /orders/`, not `/orders/pending`.**

        `/orders/pending` is a POST that places one. There is no GET at that
        path, so asking for it is a 404 - which a client treating 404 as empty
        reads as "no resting orders", the one answer that is never checked
        against anything.
        """
        return _as_rows(await self.get("/orders/", symbol=symbol))

    async def deals(
        self, from_date: datetime, to_date: datetime, position: int | None = None
    ) -> list[dict[str, Any]]:
        """Fills in a window. **`from_date`/`to_date`.**"""
        return _as_rows(
            await self.get("/history/deals", from_date=iso(from_date), to_date=iso(to_date), position=position)
        )

    async def history_orders(
        self, from_date: datetime, to_date: datetime, ticket: int | None = None
    ) -> list[dict[str, Any]]:
        """Finished orders in a window, or one by ticket."""
        return _as_rows(
            await self.get("/history/orders", from_date=iso(from_date), to_date=iso(to_date), ticket=ticket)
        )

    # -------------------------------------------------------------- writes

    async def market_order(self, body: dict[str, Any]) -> dict[str, Any]:
        """Place at market. **`sl` is required by the model, not optional.**

        `MarketOrderRequest.sl` is a bare `float` with no default, so a body
        without it is a 422 and the order is never sent. `0.0` is how MT5
        spells "no stop", and the tool layer sends it rather than omitting the
        field, so an agent that wants no stop gets an order instead of a
        validation error.

        `order_type` here is validated against `^(BUY|SELL)$` and the handler
        calls `send_market_order`, which takes no price at all. A limit sent
        to this route is accepted and filled at the market - the opposite of
        what a resting entry is for - which is why the tool layer has a
        separate pending-order tool rather than one tool with an optional
        price.
        """
        return _as_dict(await self.post_body("/trading/order", body))

    async def pending_order(self, body: dict[str, Any]) -> dict[str, Any]:
        """Place a resting order: the four limit/stop types and the two stop-limits."""
        return _as_dict(await self.post_body("/orders/pending", body))

    async def modify_position(self, body: dict[str, Any]) -> dict[str, Any]:
        """Move an open position's stop or target, addressed by **ticket**.

        `POST /positions/modify`, not `/trading/modify-sl-tp`. The latter takes
        a `trade_id` from the bridge's own database and so cannot touch a
        position it did not record - which excludes every position opened
        before a redeploy or placed through the terminal directly.
        """
        return _as_dict(await self.post_body("/positions/modify", body))

    async def modify_pending(self, ticket: int, body: dict[str, Any]) -> dict[str, Any]:
        """Move a resting order. `PUT /orders/{ticket}`, and `price` is required.

        A resting order is not a position: sending one to `/positions/modify`
        posts its limit price as a take-profit and leaves the order where it
        was.
        """
        return _as_dict(await self.put_body(f"/orders/{int(ticket)}", body))

    async def close_position(
        self, ticket: int, volume: float | None = None, type_filling: str | None = None
    ) -> dict[str, Any]:
        """Close all or part of a position. **Query parameters, not a body.**

        `volume` short of the position's own is a partial close; omitted
        closes the lot.
        """
        return _as_dict(
            await self.post_query(
                "/positions/close", ticket=int(ticket), volume=volume, type_filling=type_filling
            )
        )

    async def close_all_positions(
        self, order_type: str = "all", magic: int | None = None, type_filling: str | None = None
    ) -> dict[str, Any]:
        """Close every position, or every one on a side, or every one with a magic.

        Query parameters, like `/positions/close`. The route answers `200`
        with a per-position result list even when some of them failed: the
        bridge logs a refusal and moves on to the next position rather than
        abandoning the rest, so the count in the reply is what *closed*, not
        what was attempted.
        """
        return _as_dict(
            await self.post_query(
                "/positions/close_all", order_type=order_type, magic=magic, type_filling=type_filling
            )
        )

    async def cancel_order(self, ticket: int) -> dict[str, Any]:
        return _as_dict(await self.delete(f"/orders/{int(ticket)}"))


def _failure(method: str, route: str, response: httpx.Response) -> dict[str, Any]:
    """The bridge's error body, unpacked into `BridgeError`'s arguments.

    The bridge answers a refused order with `400` and a body carrying `error`
    and `code`, where `code` is MT5's retcode. A client that reported only the
    status would turn "volume 0.001 is below this symbol's minimum" into
    "400 Bad Request", which is the same sentence a malformed URL produces.
    """
    detail: Any = None
    try:
        detail = response.json()
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        message = detail.get("error") or detail.get("detail") or response.text
        return {
            "message": f"{method} {route} failed: {message}",
            "status": response.status_code,
            "retcode": _as_int(detail.get("code")),
            "mt5_code": _as_int(detail.get("mt5_code")),
        }
    return {
        "message": f"{method} {route} failed with {response.status_code}: {response.text[:400]}",
        "status": response.status_code,
    }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _as_rows(raw: Any) -> list[dict[str, Any]]:
    """A route's rows, whether it answered a list or a dict wrapping one.

    `/symbols/rates/*` and `/positions/` answer bare lists; `/symbols/ticks`
    answers `{"ticks": {...}}`. Both shapes are real and the difference is per
    route, so it is absorbed here rather than remembered per call site.
    """
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        for key in ("data", "rates", "ticks", "positions", "orders", "deals", "results"):
            inner = raw.get(key)
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
    return []
