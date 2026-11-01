"""The EA's tick feed: a listener, and a fan-out to whoever is subscribed.

## What this replaces

`routers/stream.py` says it plainly in its own docstring: "MT5 has no push
API, so this polls - once, centrally." That is true of the *Python* side.
Inside the terminal `OnTick` is a real callback, so the push can start there,
and `MT5/ea/TillTickFeed.mq5` is the EA that does it.

This is the other end of that socket. The EA connects **out** to this
listener, because a program under Wine cannot usefully accept inbound
connections, and sends one line of JSON per tick. This parses the lines and
hands them to whatever is subscribed - today `/api/v1/stream`.

The result is a feed whose rate is set by the market rather than by a timer.
The poll remains, for every symbol no EA is feeding.

## Why a plain socket and not a websocket

MQL5 has no websocket client. A websocket would mean writing the HTTP upgrade
and the frame masking by hand in MQL5, which is a protocol implementation in
the one language in this stack with no test framework, carrying the live tick
feed. One line of JSON per tick needs no protocol at all. The websocket stays
between this process and its consumers, which are Python and can speak it.

## What it refuses to do

**It does not fall back to polling when no EA is connected**, and that is the
point rather than an omission. A consumer that asked for a push and silently
got a 0.25s sample would take every measurement on a feed that is not the one
it thinks it has, and nothing about the result would look wrong. `sources()`
says which symbols have an EA, and the caller decides.

**It does not filter.** The poll sends only changes, because a poll that
re-sent an unchanged tick would be sending nothing. A push has nothing to
filter - every frame is a tick that actually happened - and dropping a repeat
would discard the two-ticks-at-one-price case, which is real and is how
volume at a level accumulates.

**It drops the oldest frame, never the newest.** A subscriber that cannot keep
up has a stale queue, and the newest quote is the only one worth having. The
alternative is an unbounded queue inside the process that holds the terminal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

#: Frames one subscriber may fall behind by before its oldest are dropped.
#:
#: A tick is superseded by the next one for the same symbol, so depth buys
#: nothing but latency: a subscriber reading from the back of a long queue is
#: reading history and calling it a quote.
BACKLOG = 512

#: Longest a line may be before the connection is dropped, in bytes.
#:
#: A frame is about 160 bytes. Anything approaching this is either not our EA
#: or a stream with no newlines in it, and either way reading further is
#: filling memory on behalf of something that is not talking to us.
MAX_LINE = 4096

#: Seconds after which a symbol with no frames is no longer considered fed.
#:
#: A quiet symbol is normal - a synthetic index out of session, an exotic
#: overnight - so this is generous. What it catches is an EA whose chart was
#: closed: the socket may stay open while no ticks arrive, and a consumer
#: waiting on a push that will never come is worse off than one that polls.
STALE_AFTER = 120.0


class Feed:
    """Ticks pushed from the terminal, and who is listening for them.

    One instance per process, held in `feed` below. It is deliberately not a
    queue per symbol: a subscriber wants one stream of whatever it asked for,
    and merging per-symbol queues at the consumer would reorder frames that
    arrived in a definite order.
    """

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        #: When each symbol last produced a frame, for `sources()`.
        self._seen: Dict[str, float] = {}
        #: The last sequence number per symbol, so a gap can be *named* here
        #: rather than inferred by every consumer independently.
        self._sequence: Dict[str, int] = {}
        self.received = 0
        self.gaps = 0
        self.dropped = 0
        self._server: Optional[asyncio.AbstractServer] = None

    # ──── the producer side ────
    async def start(self, host: str, port: int) -> None:
        """Listen for the EA. Idempotent, so a reload does not stack servers."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._serve, host, port)
        logger.info(f"feed: listening for the EA on {host}:{port}")

    async def stop(self) -> None:
        if self._server is None:
            return
        server, self._server = self._server, None
        server.close()
        await server.wait_closed()
        logger.info("feed: listener stopped")

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One EA connection, one symbol, until it goes away."""
        peer = writer.get_extra_info("peername")
        logger.info(f"feed: EA connected from {peer}")
        try:
            while True:
                try:
                    raw = await reader.readuntil(b"\n")
                except asyncio.IncompleteReadError:
                    break
                except asyncio.LimitOverrunError:
                    # A line longer than the stream limit means this is not
                    # our EA, or its newlines are missing. Reading on would be
                    # filling memory for something that is not talking to us.
                    logger.warning(f"feed: oversized line from {peer}, closing")
                    break
                if len(raw) > MAX_LINE:
                    logger.warning(f"feed: {len(raw)} byte line from {peer}, closing")
                    break
                self._accept(raw)
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        except Exception as exc:  # a bad producer must not take the API down
            logger.warning(f"feed: connection from {peer} failed: {exc}")
        finally:
            logger.info(f"feed: EA from {peer} disconnected")
            writer.close()

    def _accept(self, raw: bytes) -> None:
        """One line from the EA, validated and fanned out."""
        try:
            frame = json.loads(raw)
        except ValueError:
            # Counted, not raised. A malformed line is the producer's bug and
            # dropping the connection over one would cost every good tick
            # after it.
            self.dropped += 1
            return
        if not isinstance(frame, dict):
            self.dropped += 1
            return
        symbol = str(frame.get("symbol") or "")
        if not symbol:
            self.dropped += 1
            return

        self.received += 1
        self._seen[symbol] = time.time()
        self._note_gap(symbol, frame)
        self._publish(frame)

    def _note_gap(self, symbol: str, frame: Dict[str, Any]) -> None:
        """Name a missed frame here, once, rather than in every consumer.

        The EA advances `seq` for every tick the terminal gave it, whether or
        not the send succeeded - so a gap is a tick that happened and did not
        arrive. The consumer's remedy is `copy_ticks_range` over the window;
        what this does is make the gap visible, because a push feed that loses
        frames silently is a sampled feed wearing a push's name.
        """
        try:
            current = int(frame.get("seq") or 0)
        except (TypeError, ValueError):
            return
        previous = self._sequence.get(symbol)
        self._sequence[symbol] = current
        if previous is None or current == previous + 1:
            return
        if current <= previous:
            # The EA restarted and its counter went back to one. Not a gap -
            # but the consumer still has to assume it missed whatever was in
            # flight, so it is reported as a reset rather than ignored.
            logger.info(f"feed: {symbol} sequence restarted at {current}")
            frame["reset"] = True
            return
        missed = current - previous - 1
        self.gaps += missed
        frame["missed"] = missed
        logger.warning(f"feed: {symbol} missed {missed} frame(s) before {current}")

    def _publish(self, frame: Dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                # Oldest out. The newest quote is the only one worth having,
                # and an unbounded queue in the process that holds the
                # terminal fails later and worse.
                try:
                    queue.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:  # pragma: no cover - raced with a reader
                self.dropped += 1

    # ──── the consumer side ────
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=BACKLOG)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def sources(self) -> Set[str]:
        """Symbols an EA is currently feeding.

        Used by the stream to decide, per symbol, whether to wait for a push
        or to poll - and to refuse a push a caller asked for that nothing is
        producing, rather than quietly polling in its place.
        """
        now = time.time()
        return {
            symbol for symbol, last in self._seen.items() if now - last <= STALE_AFTER
        }

    def feeding(self, symbol: str) -> bool:
        return symbol in self.sources()

    @property
    def tally(self) -> Dict[str, Any]:
        """What arrived, what was lost, and who is feeding."""
        return {
            "received": self.received,
            "gaps": self.gaps,
            "dropped": self.dropped,
            "subscribers": len(self._subscribers),
            "symbols": sorted(self.sources()),
        }


#: The process's feed. One listener, many subscribers.
feed = Feed()
