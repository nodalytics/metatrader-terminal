"""A WebSocket that pushes what a trading client would otherwise poll for.

REST is the wrong shape for a price feed. A client that wants to know the bid
asks for it, gets an answer that was true when it was sent, and asks again —
so the rate it learns things is set by how often it asks rather than by how
often they change. Between polls it is blind, and every poll that returns an
unchanged tick is a round trip spent finding out nothing.

    ws://host:8000/api/v1/stream?api_key=...

    -> {"action": "subscribe", "symbols": ["XAUUSD", "BTCUSD"]}
    <- {"type": "subscribed", "symbols": ["XAUUSD", "BTCUSD"]}
    <- {"type": "tick", "symbol": "XAUUSD", "bid": 4400.0, "ask": 4400.5, ...}
    <- {"type": "positions", "positions": [...]}
    <- {"type": "account", "equity": 10002.5, ...}

## Pushed where an EA is feeding, polled where one is not

**MT5 has no push API on the Python side.** `symbol_info_tick` is a question,
not a subscription, and there is no callback to register — which is why this
route polled for everything and said so.

Inside the terminal that limitation does not exist: `OnTick` is a real
callback. So `MT5/ea/TillTickFeed.mq5` connects out to `services/feed.py` and
pushes one line of JSON per tick, and this route prefers that where it is
available. A symbol with an EA is **pushed at the rate the market moves**; a
symbol without one is polled exactly as before, centrally, in the process that
already holds the terminal connection.

The difference is not latency alone. A poll is a *sample*: a quote that moved
and moved back between two polls never happened as far as the consumer is
concerned, and no interval fixes that. A push carries every tick the terminal
saw, and the `seq` counter makes a lost frame visible rather than silent.

**`source=ea` or `source=poll` is on every frame**, and a client asking for
`require=ea` is refused rather than quietly polled. A consumer that asked for
a push and silently got a sample would take every measurement on a feed that
is not the one it believes it has, and nothing about the result would look
wrong.

**Only changes are sent**, and that is the property that makes this cheap
rather than a firehose. A quiet symbol costs nothing; a busy one is sent as
fast as it moves. The comparison is on the quote itself — bid, ask and the
millisecond stamp — because a tick that repeats a price is not news.

Positions and the account are polled on a slower loop for the same reason a
scalper needs them at all: a stop hit server-side produces no message anywhere,
and the position simply stops being there. Sending them only when the set
changes turns "poll every few seconds forever" into "say something when a trade
opens or closes".

## What this is careful about

**One bad symbol must not take the socket down.** A name the broker does not
carry, or one that stops quoting, is reported once and skipped thereafter.

**The terminal is blocking.** Every MT5 call goes through a thread so the event
loop keeps serving other sockets while one waits on the IPC pipe.

**Back-pressure is the client's problem, not the server's.** Frames are sent
with a timeout; a client that stops reading is disconnected rather than allowed
to grow an unbounded queue inside this process.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.services.feed import feed
from app.services.mt5_service import mt5_service
from app.utils.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Stream"])

#: How often the quote loop asks the terminal. A floor as well as a default:
#: below this the IPC pipe is the bottleneck and the extra calls buy nothing
#: but contention with the REST handlers sharing it.
TICK_INTERVAL = 0.25
MIN_INTERVAL = 0.1

#: Positions and account. Slower on purpose — these change when a trade opens
#: or closes, not tick by tick, and polling them at quote speed would triple
#: the load on the pipe for information that is nearly always identical.
STATE_INTERVAL = 2.0

#: Seconds to wait on a send before deciding the client is not reading.
SEND_TIMEOUT = 5.0

#: Symbols one socket may follow. A client asking for four hundred is asking
#: the terminal for four hundred quotes every quarter second.
MAX_SYMBOLS = 50


def _authorised(api_key: Optional[str], header_key: Optional[str]) -> bool:
    """Same key as the REST routes, from either the header or the query.

    Browsers cannot set headers on a WebSocket handshake, so the query
    parameter is not laziness — it is the only way a browser client can
    authenticate at all. Where a header can be sent it is preferred, and when
    no key is configured the service is open exactly as the REST side is.
    """
    expected = settings.api_key
    if not expected:
        return True
    return (header_key or api_key) == expected


def _quote_of(tick: Dict[str, Any]) -> tuple:
    """The part of a tick that decides whether it is news."""
    return (tick.get("bid"), tick.get("ask"), tick.get("time_msc") or tick.get("time"))


def _position_digest(positions: List[Dict[str, Any]]) -> tuple:
    """What has to change before the position set is worth resending.

    Ticket and volume catch opens and closes; the stop and target catch a
    trailing stop being moved. Floating profit is deliberately **not** in here
    — it changes on every tick, and including it would turn the slow loop into
    a second quote feed.
    """
    return tuple(
        sorted(
            (p.get("ticket"), p.get("volume"), p.get("sl"), p.get("tp"))
            for p in positions
        )
    )


class Subscription:
    """One socket's view: what it follows and what it has already been told."""

    def __init__(self) -> None:
        self.symbols: Set[str] = set()
        self.last_quote: Dict[str, tuple] = {}
        self.failed: Set[str] = set()

    def follow(self, names: List[str]) -> List[str]:
        for name in names[:MAX_SYMBOLS]:
            clean = str(name).strip().upper()
            if clean:
                self.symbols.add(clean)
        return sorted(self.symbols)

    def drop(self, names: List[str]) -> List[str]:
        for name in names:
            clean = str(name).strip().upper()
            self.symbols.discard(clean)
            self.last_quote.pop(clean, None)
            self.failed.discard(clean)
        return sorted(self.symbols)


async def _send(socket: WebSocket, payload: Dict[str, Any]) -> bool:
    """Send one frame. False when the client has stopped reading."""
    try:
        await asyncio.wait_for(socket.send_json(payload), timeout=SEND_TIMEOUT)
        return True
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        return False
    except Exception as exc:  # a broken socket is not an application error
        logger.debug(f"stream: send failed: {exc}")
        return False


async def _pump_pushed(socket: WebSocket, sub: Subscription) -> None:
    """Relay the EA's frames, unfiltered, for the symbols this socket follows.

    **No change filter, unlike the poll.** A poll that re-sent an unchanged
    tick would be sending nothing, so it compares. A push has nothing to
    compare: every frame is a tick the terminal actually saw, and dropping a
    repeat would discard two ticks at one price - which is real, and is how
    volume at a level accumulates.
    """
    queue = feed.subscribe()
    try:
        while True:
            frame = await queue.get()
            symbol = str(frame.get("symbol") or "")
            if symbol not in sub.symbols:
                continue
            if not await _send(socket, frame):
                return
    finally:
        feed.unsubscribe(queue)


async def _pump_ticks(socket: WebSocket, sub: Subscription, interval: float) -> None:
    """Poll quotes for the followed symbols an EA is **not** feeding.

    A symbol gains and loses its EA while this runs - a chart is closed, a
    terminal restarts - so the set is re-read every pass rather than once at
    subscribe time. The cost is a set lookup per symbol per pass; the
    alternative is a symbol that falls silent when its EA goes away.
    """
    while True:
        pushed = feed.sources()
        for symbol in sorted(sub.symbols):
            if symbol in sub.failed or symbol in pushed:
                continue
            try:
                tick = await run_in_threadpool(
                    mt5_service.get_symbol_info_tick, symbol, False
                )
            except Exception as exc:
                # Said once, then the symbol is left alone. A name the broker
                # does not carry would otherwise raise on every pass forever.
                sub.failed.add(symbol)
                await _send(
                    socket,
                    {"type": "error", "symbol": symbol, "message": str(exc)},
                )
                continue
            if not tick:
                continue
            quote = _quote_of(tick)
            if sub.last_quote.get(symbol) == quote:
                continue
            sub.last_quote[symbol] = quote
            # `source` on every frame, so a consumer never has to infer which
            # feed it is reading. The push sets its own; this is the other.
            frame = {"type": "tick", "symbol": symbol, "source": "poll"}
            frame.update(tick)
            frame["source"] = "poll"
            if not await _send(socket, frame):
                return
        await asyncio.sleep(interval)


async def _pump_state(socket: WebSocket, magic: Optional[int]) -> None:
    """Push positions and the account, when either changes."""
    last_positions: Optional[tuple] = None
    last_account: Optional[tuple] = None
    while True:
        try:
            positions = await run_in_threadpool(mt5_service.get_positions, magic)
        except Exception as exc:
            logger.debug(f"stream: positions failed: {exc}")
            positions = None

        if positions is not None:
            digest = _position_digest(positions)
            if digest != last_positions:
                last_positions = digest
                if not await _send(
                    socket, {"type": "positions", "positions": positions}
                ):
                    return

        try:
            account = await run_in_threadpool(mt5_service.get_account_info)
        except Exception as exc:
            logger.debug(f"stream: account failed: {exc}")
            account = None

        if account is not None:
            info = account._asdict() if hasattr(account, "_asdict") else dict(account)
            # Equity and free margin move with open trades; balance and
            # leverage do not. Rounded so a cent of floating profit does not
            # count as a change worth a frame.
            digest = (
                round(float(info.get("equity", 0.0)), 2),
                round(float(info.get("balance", 0.0)), 2),
                round(float(info.get("margin_free", 0.0)), 2),
            )
            if digest != last_account:
                last_account = digest
                frame = {"type": "account"}
                frame.update(info)
                if not await _send(socket, frame):
                    return

        await asyncio.sleep(STATE_INTERVAL)


async def _read_commands(socket: WebSocket, sub: Subscription) -> None:
    """Handle what the client asks for, until it goes away."""
    while True:
        message = await socket.receive_json()
        action = str(message.get("action", "")).lower()
        symbols = message.get("symbols") or []
        if not isinstance(symbols, list):
            symbols = [symbols]

        if action == "subscribe":
            await _send(socket, {"type": "subscribed", "symbols": sub.follow(symbols)})
        elif action == "unsubscribe":
            await _send(socket, {"type": "subscribed", "symbols": sub.drop(symbols)})
        elif action == "ping":
            await _send(socket, {"type": "pong"})
        else:
            await _send(
                socket,
                {
                    "type": "error",
                    "message": f"unknown action {action!r}",
                    "actions": ["subscribe", "unsubscribe", "ping"],
                },
            )


@router.websocket("/stream")
async def stream(
    websocket: WebSocket,
    api_key: Optional[str] = Query(None),
    symbols: Optional[str] = Query(None, description="Comma-separated, to start immediately."),
    magic: Optional[int] = Query(None, description="Only positions carrying this magic."),
    interval: float = Query(TICK_INTERVAL, description="Seconds between quote polls."),
    require: Optional[str] = Query(
        None,
        description="Set to `ea` to be refused unless every symbol has an EA pushing.",
    ),
):
    """Quotes, positions and account state, pushed as they change."""
    header_key = websocket.headers.get("x-api-key")
    if not _authorised(api_key, header_key):
        # 1008 is "policy violation", which is the closest thing a WebSocket
        # has to a 401. Closing before accept would give the client no reason.
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "invalid or missing API key"})
        await websocket.close(code=1008)
        return

    await websocket.accept()
    sub = Subscription()
    if symbols:
        sub.follow([s for s in symbols.split(",") if s.strip()])

    pushed = feed.sources()
    if str(require or "").lower() == "ea":
        # **Refused rather than quietly polled.** A consumer that asked for a
        # push and got a 0.25s sample would take every measurement on a feed
        # that is not the one it thinks it has, and nothing about the answer
        # would look wrong. Naming the symbols that are missing makes it a
        # deployment problem - a chart that is not open - rather than a
        # mystery.
        missing = sorted(set(sub.symbols) - pushed)
        if missing or not sub.symbols:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "no EA is feeding these symbols",
                    "symbols": missing or sorted(sub.symbols),
                    "detail": "attach MT5/ea/TillTickFeed.mq5 to one chart per symbol",
                }
            )
            await websocket.close(code=1008)
            return

    await _send(
        websocket,
        {
            "type": "ready",
            "symbols": sorted(sub.symbols),
            "interval": max(interval, MIN_INTERVAL),
            # So a consumer can tell, at connect time, which of its symbols it
            # is about to be sampled on.
            "pushed": sorted(set(sub.symbols) & pushed),
        },
    )

    tasks = [
        asyncio.create_task(_pump_pushed(websocket, sub)),
        asyncio.create_task(_pump_ticks(websocket, sub, max(interval, MIN_INTERVAL))),
        asyncio.create_task(_pump_state(websocket, magic)),
        asyncio.create_task(_read_commands(websocket, sub)),
    ]
    try:
        # Whichever finishes first ends the connection: the reader returning
        # means the client has gone, and a pump returning means the socket
        # stopped accepting frames.
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            # Retrieved so a disconnect does not surface later as "task
            # exception was never retrieved" from somewhere unrelated.
            if not task.cancelled() and task.exception() is not None:
                raise task.exception()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        logger.warning(f"stream: closing after {exc}")
    finally:
        await _stop(tasks)


async def _stop(tasks) -> None:
    """Cancel the pumps and wait for them, without propagating cancellation.

    This runs while the socket is going away, which is frequently *because* the
    surrounding task is itself being cancelled. Awaiting the children then
    re-raises `CancelledError` at the await, out of a `finally`, and the client
    sees a crash instead of a closed connection. Suppressing it here is the
    difference between a clean disconnect and an error on every hang-up.
    """
    for task in tasks:
        task.cancel()
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass
