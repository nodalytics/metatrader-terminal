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

## MT5 has no push API, so this polls — once, centrally

`symbol_info_tick` is a question, not a subscription; there is no callback to
register. What changes here is *who* polls and how often the result travels: a
single loop inside the process that already holds the terminal connection,
pushing only when something is different, instead of N clients each asking over
HTTP on their own timer.

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


async def _pump_ticks(socket: WebSocket, sub: Subscription, interval: float) -> None:
    """Push quotes for the followed symbols, whenever they differ."""
    while True:
        for symbol in sorted(sub.symbols):
            if symbol in sub.failed:
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
            frame = {"type": "tick", "symbol": symbol}
            frame.update(tick)
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
    await _send(
        websocket,
        {"type": "ready", "symbols": sorted(sub.symbols), "interval": max(interval, MIN_INTERVAL)},
    )

    tasks = [
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
