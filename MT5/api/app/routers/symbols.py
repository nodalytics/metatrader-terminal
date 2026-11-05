from fastapi import APIRouter, HTTPException, Query
from app.services.mt5_service import mt5_service
from app.utils.exceptions import MT5SymbolNotFoundError
from typing import Any, Dict, List, Optional
from datetime import datetime
from app.services.terminal_transport import mt5

#: 422, as a literal.
#:
#: Starlette renamed `HTTP_422_UNPROCESSABLE_ENTITY` to `..._CONTENT`, and
#: `requirements.txt` pins neither `fastapi` nor `starlette`. So two installs
#: that both satisfy the requirements disagree about which name exists, and on
#: the older one `status.HTTP_422_UNPROCESSABLE_CONTENT` raises
#: `AttributeError` *inside the handler* - which the exception middleware turns
#: into a **500** at exactly the point the route meant to explain a bad
#: request. Measured on starlette 0.27.0; CI resolves a newer one and passes.
UNPROCESSABLE = 422

router = APIRouter(prefix="/symbols", tags=["Symbols"])


@router.get("/", response_model=List[str])
def get_all_symbols():
    return mt5_service.get_symbols()


@router.get("/info")
def get_symbol_info_query(symbol: str = Query(...)):
    return mt5_service.get_symbol_info(symbol)


@router.get(
    "/info/{symbol}",
    summary="Symbol specification - NOT a live quote",
    responses={
        200: {
            "description": (
                "**`bid`/`ask` on this route is a snapshot, not a quote.** It is "
                "part of the symbol's specification and the terminal refreshes it "
                "on its own terms: measured 2026-11-04, eight reads three seconds "
                "apart returned the same bid, the same ask **and the same `time`**, "
                "while `/symbols/ticks/{symbol}` moved on every one. A client "
                "polling here published 7 quotes from 2,399 polls. Use "
                "`/symbols/ticks/{symbol}` for a sampled quote, or the WebSocket "
                "at `/stream` for a complete one.\n\n"
                "**`filling_mode` is a bitmask and belongs to the symbol**: 1 FOK, "
                "2 IOC, 4 RETURN. Pick `type_filling` from it. Sending a mode the "
                "symbol does not list returns `10030, Unsupported filling mode` on "
                "every order."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "name": "EURUSD",
                        "digits": 5,
                        "point": 1e-05,
                        "volume_min": 0.01,
                        "volume_max": 500.0,
                        "volume_step": 0.01,
                        "filling_mode": 1,
                        "trade_mode": 4,
                        "bid": 1.12217,
                        "ask": 1.12225,
                    }
                }
            },
        }
    },
)
def get_symbol_info_path(symbol: str):
    """The symbol's contract: digits, volume bounds, and `filling_mode`.

    Case-sensitive, in the broker's own spelling - `EURUSD` works where
    `eurusd` is a 404, which once left a desk with quotes and no trades.
    """
    return mt5_service.get_symbol_info(symbol)


@router.post("/select/{symbol}")
def select_symbol(symbol: str):
    selected = mt5_service.select_symbol(symbol)
    if not selected:
        raise MT5SymbolNotFoundError(f"Failed to select symbol '{symbol}'")
    return {"symbol": symbol, "selected": True}


#: Most symbols one request may ask for. Each one costs a `symbol_select` and a
#: `symbol_info_tick` into the terminal, which is a synchronous IPC round trip,
#: so an unbounded batch is a way to hold the event loop for an unbounded time.
#: Forty is comfortably above the ~30 the desk watches and well below anything
#: that would block noticeably.
MAX_TICK_BATCH = 40


@router.get("/ticks")
def get_symbol_ticks(symbols: str, use_cache: bool = True):
    """Quotes for many symbols in one request.

    **Why this exists.** `/ticks/{symbol}` is one HTTP round trip per symbol,
    and a consumer watching thirty instruments therefore pays thirty of them per
    sample. Over an SSH tunnel to another continent that is the dominant cost of
    a quote, and the desk was observed shedding its backlog - `quote backlog
    full at 5000 - shed 7000 oldest` - while the bridge itself was idle. One
    request for thirty symbols removes twenty-nine round trips.

    **Partial success is the point.** A batch that fails because one symbol in
    it is unknown is a batch you cannot use: a caller would have to fall back to
    asking one at a time, which is the thing being avoided. So each symbol
    succeeds or fails on its own and both are reported. `ticks` carries what was
    found, `errors` carries a message per symbol that was not, and the status is
    200 as long as the request itself was well formed - including when nothing
    at all resolved, because "none of these thirty symbols exist" is an answer.

    `use_cache` defaults to `True` to match `/ticks/{symbol}`; a streaming
    caller should pass `False` for the reason `get_symbol_info_tick` documents -
    a cached tick repeated is worse than no tick.
    """
    from app.services.market_data import resolve_symbols

    # **Cheap work first, and the cap before the terminal is touched.** The
    # split, the dedupe and the limit all run on the caller's own strings;
    # resolving is a terminal lookup per name, so doing it first would let a
    # caller pay for a thousand lookups and only then be told the batch is too
    # large.
    asked = [s.strip() for s in symbols.split(",") if s.strip()]
    if not asked:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="symbols must name at least one symbol",
        )
    # Deduplicate but keep the caller's order, so the response reads like the
    # request. A repeated symbol is a caller bug, not ours, and silently
    # charging them two IPC round trips for it is unhelpful.
    seen: set[str] = set()
    asked = [s for s in asked if not (s in seen or seen.add(s))]
    if len(asked) > MAX_TICK_BATCH:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=f"asked for {len(asked)} symbols; the limit is {MAX_TICK_BATCH}",
        )

    # **Resolved against the terminal, not upper-cased.** MT5 symbol names are
    # case-sensitive and many are not upper case - `Germany 40`, `US Tech 100`,
    # every Deriv synthetic - so `.upper()` reported "not found" for symbols the
    # terminal holds. Resolution keeps the old convenience that `xauusd` finds
    # `XAUUSD` without breaking the rest, and is deduplicated again because two
    # spellings of one symbol resolve to the same name.
    seen = set()
    ordered = [
        s for s in resolve_symbols(asked) if not (s in seen or seen.add(s))
    ]

    ticks: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for name in ordered:
        try:
            ticks[name] = mt5_service.get_symbol_info_tick(name, use_cache=use_cache)
        except Exception as exc:
            # Deliberately broad. One symbol's failure must not decide the fate
            # of the other twenty-nine, and the terminal raises several
            # different things for "no".
            errors[name] = str(exc)
    return {"ticks": ticks, "errors": errors, "requested": len(ordered)}


@router.get("/ticks/{symbol}")
def get_symbol_tick(symbol: str):
    return mt5_service.get_symbol_info_tick(symbol)


@router.get("/{symbol}")
def get_symbol(symbol: str):
    return mt5_service.get_symbol_info(symbol)


@router.get("/rates/from")
def fetch_data_from(symbol: str, timeframe: str, date_from: datetime, count: int = 100):
    """`count` bars **ending** at `date_from`, counting backward.

    This mirrors MQL5's `CopyRates` and it is the opposite of how the parameter
    name reads. `date_from` is the *newest* bar of the sample, not the oldest, so
    `date_from=2026-09-01&count=3000` answers with 3,000 bars running from
    2026-02-27 to 2026-09-01.

    Asking with a `date_from` at the very beginning of a symbol's history is
    therefore correct behaviour that looks like a bug: there is nothing before it,
    so one bar comes back whatever `count` says. One client read that as "the
    route ignores its count" and wrote it down. To page *forward* through history,
    walk backwards from the newest bar you hold, or use `/rates/range`.
    """
    return mt5_service.copy_rates_from(symbol, timeframe, date_from, count)


@router.get("/rates/pos")
def fetch_data_pos(
    symbol: str,
    timeframe: str = "M1",
    num_bars: int = 100,
    start_pos: int = 0,
    count: Optional[int] = Query(None, include_in_schema=False),
):
    """`num_bars` closed bars ending `start_pos` back from now, newest last.

    The last element is the **forming** bar - its close is simply the current
    price - so a caller reading a pattern from it is making a claim the next tick
    can withdraw. **`start_pos=1` excludes it**, which is what a caller wanting
    only closed bars means; it used to have no way to say so and had to drop the
    newest row itself, which every client then had to remember.

    `count` is accepted as an alias for `num_bars` and hidden from the schema.
    FastAPI drops a parameter it does not declare, so a caller sending `count`
    - which the adapter in this repository did for months - got the default of
    100 bars with a 200 and no warning. One name is blessed; the other is
    honoured rather than silently ignored.

    There is a practical ceiling on `num_bars` that is not a limit on history:
    every bar becomes a dict and then JSON, inside a container with a memory
    limit, so a large enough request fails in the terminal rather than returning
    fewer bars. Measured on this deployment, 50,000 answers and 100,000 does not.
    A failure is now a 502 carrying MT5's own error, not a 404.
    """
    wanted = int(count) if count is not None and num_bars == 100 else int(num_bars)
    return mt5_service.copy_rates_from_pos(symbol, timeframe, int(start_pos), wanted)


@router.get("/rates/range")
def fetch_data_range(
    symbol: str,
    timeframe: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    date_from: Optional[datetime] = Query(None, include_in_schema=False),
    date_to: Optional[datetime] = Query(None, include_in_schema=False),
    from_date: Optional[datetime] = Query(None, include_in_schema=False),
    to_date: Optional[datetime] = Query(None, include_in_schema=False),
):
    """Every bar between two dates - the unambiguous way to ask for a window.

    Preferred over `/rates/from` for history, because the window is stated rather
    than counted from one end. A window with no bars in it answers `[]`; it is the
    same response-size ceiling as `/rates/pos` that decides how wide a window can
    be asked for at once.

    **This bridge spelled one window three ways.** `/history/*` took
    `from_date`/`to_date`, `/symbols/ticks/{s}/range` took `date_from`/`date_to`,
    and this route took `start`/`end`. A caller who learned one spelling got a
    silent empty from the next, because FastAPI drops a parameter it does not
    declare. All three are accepted here and on the tick range; `start`/`end`
    remain the documented pair, since blessing one name is the point of having
    one.
    """
    first = start or date_from or from_date
    last = end or date_to or to_date
    if first is None or last is None:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="a window needs both ends: start/end (or date_from/date_to, from_date/to_date)",
        )
    return mt5_service.copy_rates_range(symbol, timeframe, first, last)


@router.get("/ticks/{symbol}/from")
def get_ticks_from(symbol: str, date_from: datetime, count: int = 1000, flags: str = "ALL"):
    data = mt5_service.copy_ticks_from(symbol, date_from, count, flags)
    if data is None:
        raise HTTPException(status_code=404, detail="No tick data found")
    return data


@router.get("/ticks/{symbol}/range")
def get_ticks_range(
    symbol: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    start: Optional[datetime] = Query(None, include_in_schema=False),
    end: Optional[datetime] = Query(None, include_in_schema=False),
    from_date: Optional[datetime] = Query(None, include_in_schema=False),
    to_date: Optional[datetime] = Query(None, include_in_schema=False),
    flags: str = "ALL",
):
    """Ticks over a window. `date_from`/`date_to` are the documented pair; the
    other two spellings this bridge uses elsewhere are accepted, because a
    parameter FastAPI does not declare is dropped and the caller gets an empty
    answer that looks like a quiet market."""
    first = date_from or start or from_date
    last = date_to or end or to_date
    if first is None or last is None:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="a window needs both ends: date_from/date_to (or start/end, from_date/to_date)",
        )
    data = mt5_service.copy_ticks_range(symbol, first, last, flags)
    if data is None:
        raise HTTPException(status_code=404, detail="No tick data found")
    return data


@router.post("/book/{symbol}/subscribe")
def subscribe_book(symbol: str):
    mt5_service.initialize()
    mt5_service.select_symbol(symbol)
    result = mt5.market_book_add(symbol)
    if not result:
        raise MT5SymbolNotFoundError(f"Failed to subscribe to book for '{symbol}'")
    return {"symbol": symbol, "subscribed": True}


@router.post("/book/{symbol}/unsubscribe")
def unsubscribe_book(symbol: str):
    mt5_service.initialize()
    result = mt5.market_book_release(symbol)
    if not result:
        raise MT5SymbolNotFoundError(f"Failed to unsubscribe from book for '{symbol}'")
    return {"symbol": symbol, "subscribed": False}


@router.get("/book/{symbol}")
def get_book(symbol: str):
    mt5_service.initialize()
    book = mt5.market_book_get(symbol)
    if book is None:
        raise HTTPException(status_code=404, detail="No book data")
    return [b._asdict() for b in book]


@router.get("/check/{symbol}")
def check_symbol(symbol: str):
    mt5_service.initialize()
    info = mt5.symbol_info(symbol)
    if not info:
        raise MT5SymbolNotFoundError(f"Symbol '{symbol}' not found")
    return {"visible": info.visible, "select": info.select, "name": info.name}
