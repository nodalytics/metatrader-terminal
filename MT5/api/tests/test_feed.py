"""The EA's tick feed: the listener, the fan-out, and the gap it names.

`routers/stream.py` used to say "MT5 has no push API, so this polls", which is
true of the Python side and not of the terminal: `OnTick` is a real callback.
`MT5/ea/TillTickFeed.mq5` pushes from there and `services/feed.py` is the
other end of that socket.

These tests drive the listener over a real loopback socket rather than calling
`_accept` directly, because the line framing is half of what this module does
and a test handed whole frames would not exercise it. They run their own loop
with `asyncio.run` rather than taking a `pytest-asyncio` dependency: this
repo's suite is integration-style, against a live container, and adding a
plugin to it for one file is the worse trade.

What they are mostly about is the two properties a push has to have and a poll
cannot: **every tick rather than a sample**, and **a lost frame that says so.**
"""

from __future__ import annotations

import asyncio
import json

from app.services.feed import BACKLOG, Feed


def frame(symbol: str = "EURUSD", seq: int = 1, bid: float = 1.2345, **extra) -> bytes:
    """One line of what the EA sends, in the shape it sends it."""
    body = {
        "type": "tick",
        "symbol": symbol,
        "bid": bid,
        "ask": bid + 0.0002,
        "last": 0.0,
        "volume": 0,
        "volume_real": 0.0,
        "time_msc": 1_700_000_000_000 + seq,
        # 134 rather than 6: MT5's flags carry an undocumented bit 128, so a
        # fixture using the documented values would not look like real data.
        "flags": 134,
        "seq": seq,
        "source": "ea",
    }
    body.update(extra)
    return (json.dumps(body) + "\n").encode()


async def send(feed: Feed, payload: bytes) -> None:
    """Connect as the EA does and send `payload`, then let the server read it."""
    await feed.start("127.0.0.1", 0)
    port = feed._server.sockets[0].getsockname()[1]
    _, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    writer.close()


async def settle(predicate, tries: int = 200) -> None:
    """Yield to the loop until `predicate` holds, or give up.

    `drain` only means the bytes left this side, so the server's reader task
    needs a turn. A bounded spin rather than a sleep, so a passing test costs
    nothing and a failing one does not hang the suite.
    """
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0)


def test_a_tick_from_the_ea_reaches_a_subscriber():
    async def run():
        feed = Feed()
        queue = feed.subscribe()
        try:
            await send(feed, frame())
            await settle(lambda: queue.qsize() >= 1)
            got = queue.get_nowait()
            assert got["symbol"] == "EURUSD"
            assert got["source"] == "ea"
            assert got["seq"] == 1
        finally:
            await feed.stop()

    asyncio.run(run())


def test_several_ticks_in_one_write_are_several_frames():
    """A socket does not preserve message boundaries, so ticks sent in quick
    succession arrive as one read. A reader that took a read for a frame would
    drop every tick after the first in each batch - a failure that looks
    exactly like a thin market."""

    async def run():
        feed = Feed()
        queue = feed.subscribe()
        try:
            await send(feed, frame(seq=1) + frame(seq=2) + frame(seq=3))
            await settle(lambda: queue.qsize() >= 3)
            assert queue.qsize() == 3
            assert [queue.get_nowait()["seq"] for _ in range(3)] == [1, 2, 3]
        finally:
            await feed.stop()

    asyncio.run(run())


def test_an_unchanged_quote_is_still_sent():
    """**The push does not filter and the poll must.** A poll that re-sent an
    unchanged tick would be sending nothing, so it compares. A push has
    nothing to compare: every frame is a tick the terminal saw, and dropping a
    repeat would discard two ticks at one price, which is real and is how
    volume at a level accumulates."""

    async def run():
        feed = Feed()
        queue = feed.subscribe()
        try:
            await send(feed, frame(seq=1, bid=1.2345) + frame(seq=2, bid=1.2345))
            await settle(lambda: queue.qsize() >= 2)
            assert queue.qsize() == 2
        finally:
            await feed.stop()

    asyncio.run(run())


def test_a_missed_frame_is_named_rather_than_inferred():
    """The EA advances `seq` for every tick the terminal gave it, whether or
    not the send succeeded - so a gap is a tick that happened and did not
    arrive. Naming it is what lets a consumer refill the window from
    `copy_ticks_range`: **a push feed that loses frames silently is a sampled
    feed wearing a push's name.**"""

    async def run():
        feed = Feed()
        queue = feed.subscribe()
        try:
            await send(feed, frame(seq=1) + frame(seq=5))
            await settle(lambda: queue.qsize() >= 2)
            queue.get_nowait()
            assert queue.get_nowait()["missed"] == 3
            assert feed.gaps == 3
        finally:
            await feed.stop()

    asyncio.run(run())


def test_a_restarted_ea_is_a_reset_and_not_a_gap():
    """Its counter goes back to one, which is not three billion missed ticks.
    The consumer still has to assume it lost whatever was in flight, so it is
    reported rather than ignored."""

    async def run():
        feed = Feed()
        queue = feed.subscribe()
        try:
            await send(feed, frame(seq=9) + frame(seq=1))
            await settle(lambda: queue.qsize() >= 2)
            queue.get_nowait()
            assert queue.get_nowait().get("reset") is True
            assert feed.gaps == 0
        finally:
            await feed.stop()

    asyncio.run(run())


def test_a_malformed_line_does_not_end_the_connection():
    """A bad line is the producer's bug, and dropping the connection over one
    would cost every good tick after it."""

    async def run():
        feed = Feed()
        queue = feed.subscribe()
        try:
            await send(feed, b"not json\n" + frame(seq=1))
            await settle(lambda: queue.qsize() >= 1)
            assert queue.get_nowait()["seq"] == 1
            assert feed.dropped == 1
        finally:
            await feed.stop()

    asyncio.run(run())


def test_a_slow_subscriber_loses_its_oldest_frames():
    """Oldest out, never newest. The newest quote is the only one worth
    having, and the alternative is an unbounded queue inside the process that
    holds the terminal - which fails later and worse."""

    async def run():
        feed = Feed()
        queue = feed.subscribe()
        for seq in range(BACKLOG + 10):
            feed._publish({"symbol": "EURUSD", "seq": seq})
        assert queue.qsize() == BACKLOG
        assert queue.get_nowait()["seq"] == 10, "the oldest ten went, not the newest"

    asyncio.run(run())


def test_a_symbol_with_no_frames_is_not_a_source():
    """A consumer waiting on a push that will never come is worse off than one
    that polls, so a symbol counts as fed only while frames arrive - an EA's
    socket can stay open after its chart is closed."""

    async def run():
        feed = Feed()
        try:
            assert feed.sources() == set()
            await send(feed, frame(symbol="XAUUSD"))
            await settle(lambda: feed.received >= 1)
            assert feed.feeding("XAUUSD")
            assert not feed.feeding("EURUSD")
        finally:
            await feed.stop()

    asyncio.run(run())


def test_starting_twice_does_not_stack_listeners():
    """A reload that bound a second listener would leave the EA connected to
    whichever one it reached first, with the other silently feeding nobody."""

    async def run():
        feed = Feed()
        try:
            await feed.start("127.0.0.1", 0)
            first = feed._server
            await feed.start("127.0.0.1", 0)
            assert feed._server is first
        finally:
            await feed.stop()

    asyncio.run(run())


def test_the_tally_says_what_arrived_and_what_was_lost():
    """A feed that cannot be read back is a feed nobody can tell is working,
    and "no ticks" looks identical to a quiet market."""

    async def run():
        feed = Feed()
        try:
            await send(feed, frame(seq=1) + b"rubbish\n" + frame(seq=4))
            await settle(lambda: feed.received >= 2)
            tally = feed.tally
            assert tally["received"] == 2
            assert tally["dropped"] == 1
            assert tally["gaps"] == 2
            assert tally["symbols"] == ["EURUSD"]
        finally:
            await feed.stop()

    asyncio.run(run())
