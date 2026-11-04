"""Consume the tick stream over a WebSocket.

    python examples/stream_ticks.py EURUSD XAUUSD
    python examples/stream_ticks.py --require-ea EURUSD

Needs `websockets` (already in `MT5/api/requirements.txt`) and an API key in
`API_KEY`, or `API_KEY_SEED` to derive one the way `examples/mt5api_client.py`
does.

## Why a socket rather than a poll

REST is the wrong shape for a price feed: a client learns things at the rate it
asks rather than the rate they change, and every poll returning an unchanged
tick is a round trip spent finding out nothing.

It is also not just a latency argument. **A poll is a sample.** A quote that
moved and moved back between two polls never happened as far as the consumer is
concerned, and no interval fixes that.

And polling the wrong route is worse than sampling. `/symbols/{symbol}` returns
the symbol's *specification*, whose `bid`/`ask` the terminal refreshes on its
own terms: measured 2026-11-04, eight reads three seconds apart gave the same
bid, ask **and `time`**. A client doing that published 7 quotes from 2,399
polls and looked like a market nobody was quoting.

## Not yet smoke-tested against a deployed bridge

`/api/v1/stream` is in this repository and **not** in the published `:latest`
image, which CI builds from `main` only - the stream router and the EA arrived
on a branch. The running terminal's OpenAPI document carries no `/stream` path
at all, so this example is written against `routers/stream.py` and its protocol
docstring rather than against a reply. Run it once the image carrying that
router is deployed.

## The two things this example exists to show

**Check `source` on every frame.** It reads `ea` where the EA is feeding that
symbol and `poll` where it is not, and the two are different feeds - one
carries every tick the terminal saw, the other is a sample. A consumer that
asked for a push and silently got a sample would take every measurement on a
feed that is not the one it believes it has, and nothing about the numbers
would look wrong. `--require-ea` refuses at the handshake instead.

**Watch `seq` for gaps.** It is the whole reason a lost frame is visible here
rather than silent. A jump means ticks went missing between the terminal and
here, and a consumer that ignores it will quietly hold an incomplete series -
which is the failure the counter was added to prevent, so not checking it
discards the benefit.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import sys

#: Grows to this and no further. A feed that is down stays down for minutes,
#: and a tight retry loop against it is a denial of service against your own
#: terminal rather than a recovery strategy.
BACKOFF_MAX = 30.0


def api_key() -> str:
    """The key, or one derived from the seed, as the REST example does."""
    found = os.getenv("API_KEY")
    if found:
        return found
    seed = os.getenv("API_KEY_SEED")
    if not seed:
        sys.exit("set API_KEY, or API_KEY_SEED to derive one")
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class Stream:
    """One connection's worth of state, so a reconnect starts clean."""

    def __init__(self, symbols: list[str], require_ea: bool) -> None:
        self.symbols = symbols
        self.require_ea = require_ea
        #: Per symbol, because each symbol's frames carry their own counter.
        self.seq: dict[str, int] = {}
        self.ticks = 0
        self.gaps = 0

    def url(self, base: str, key: str) -> str:
        where = base.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        query = f"api_key={key}&symbols={','.join(self.symbols)}"
        if self.require_ea:
            # Refused unless every symbol has an EA pushing, rather than
            # quietly served a polled sample.
            query += "&require=ea"
        return f"{where}/api/v1/stream?{query}"

    def on_tick(self, frame: dict) -> None:
        symbol = str(frame.get("symbol") or "?")
        self.ticks += 1

        # **The gap check.** `seq` is per symbol and increments by one; a jump
        # means frames were lost on the way here. Refill the hole from
        # `/symbols/ticks/{symbol}/range` if completeness matters to you.
        seq = frame.get("seq")
        if seq is not None:
            last = self.seq.get(symbol)
            if last is not None and int(seq) > last + 1:
                missed = int(seq) - last - 1
                self.gaps += missed
                print(f"  ! {symbol}: {missed} frame(s) lost (seq {last} -> {seq})")
            self.seq[symbol] = int(seq)

        source = frame.get("source", "?")
        if source != "ea" and self.require_ea:
            # Should be unreachable: the handshake refuses. Checked anyway,
            # because the alternative to a loud check here is a silent sample.
            print(f"  ! {symbol}: asked for an EA feed and this frame says {source!r}")
        print(
            f"  {symbol:>10}  bid {frame.get('bid')}  ask {frame.get('ask')}  "
            f"[{source}] seq={seq}"
        )

    def on_frame(self, frame: dict) -> bool:
        """Handle one frame. False means the server told us to stop."""
        kind = frame.get("type")
        if kind == "tick":
            self.on_tick(frame)
        elif kind == "subscribed":
            print(f"subscribed: {frame.get('symbols')}")
        elif kind == "positions":
            print(f"positions changed: {len(frame.get('positions') or [])} open")
        elif kind == "account":
            print(f"account: equity {frame.get('equity')}")
        elif kind == "error":
            # A bad symbol is reported once and skipped, so this is not
            # necessarily fatal - but an auth failure arrives this way too and
            # is followed by a close.
            print(f"server error: {frame.get('message')} {frame.get('detail') or ''}")
            return not str(frame.get("message", "")).lower().startswith("invalid")
        else:
            print(f"frame: {frame}")
        return True

    async def run_once(self, base: str, key: str) -> None:
        import websockets

        async with websockets.connect(self.url(base, key)) as socket:
            # The query string already subscribed. This is the explicit form, for
            # a client that discovers symbols after connecting.
            await socket.send(json.dumps({"action": "subscribe", "symbols": self.symbols}))
            async for raw in socket:
                if not self.on_frame(json.loads(raw)):
                    return


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="+", help="broker spelling, case-sensitive")
    parser.add_argument("--url", default=os.getenv("MT5_API_URL", "http://localhost:8000"))
    parser.add_argument(
        "--require-ea",
        action="store_true",
        help="refuse at the handshake unless every symbol has an EA pushing, "
        "rather than be served a polled sample",
    )
    args = parser.parse_args()

    stream = Stream(args.symbols, args.require_ea)
    key = api_key()
    backoff = 1.0
    while True:
        try:
            await stream.run_once(args.url, key)
            print("server closed the socket")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"connection lost: {type(exc).__name__}: {exc}")
        print(
            f"  ({stream.ticks} ticks, {stream.gaps} lost) reconnecting in {backoff:.0f}s"
        )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, BACKOFF_MAX)
        # **Dropped, not kept.** A reconnect is exactly where a gap is
        # expected, and comparing a new connection's counter against the old
        # one's would report a hole that is an artefact of reconnecting.
        stream.seq.clear()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
