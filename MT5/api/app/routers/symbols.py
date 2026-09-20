from fastapi import APIRouter, HTTPException, Query
from app.services.mt5_service import mt5_service
from app.utils.exceptions import MT5SymbolNotFoundError
from typing import List
from datetime import datetime
import MetaTrader5 as mt5

router = APIRouter(prefix="/symbols", tags=["Symbols"])


@router.get("/", response_model=List[str])
def get_all_symbols():
    return mt5_service.get_symbols()


@router.get("/info")
def get_symbol_info_query(symbol: str = Query(...)):
    return mt5_service.get_symbol_info(symbol)


@router.get("/info/{symbol}")
def get_symbol_info_path(symbol: str):
    return mt5_service.get_symbol_info(symbol)


@router.post("/select/{symbol}")
def select_symbol(symbol: str):
    selected = mt5_service.select_symbol(symbol)
    if not selected:
        raise MT5SymbolNotFoundError(f"Failed to select symbol '{symbol}'")
    return {"symbol": symbol, "selected": True}


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
def fetch_data_pos(symbol: str, timeframe: str = "M1", num_bars: int = 100):
    """The most recent `num_bars` closed bars, newest last.

    The last element is the **forming** bar - its close is simply the current
    price - so a caller reading a pattern from it is making a claim the next tick
    can withdraw.

    There is a practical ceiling on `num_bars` that is not a limit on history:
    every bar becomes a dict and then JSON, inside a container with a memory
    limit, so a large enough request fails in the terminal rather than returning
    fewer bars. Measured on this deployment, 50,000 answers and 100,000 does not.
    A failure is now a 502 carrying MT5's own error, not a 404.
    """
    return mt5_service.copy_rates_from_pos(symbol, timeframe, 0, num_bars)


@router.get("/rates/range")
def fetch_data_range(symbol: str, timeframe: str, start: datetime, end: datetime):
    """Every bar between two dates - the unambiguous way to ask for a window.

    Preferred over `/rates/from` for history, because the window is stated rather
    than counted from one end. A window with no bars in it answers `[]`; it is the
    same response-size ceiling as `/rates/pos` that decides how wide a window can
    be asked for at once.
    """
    return mt5_service.copy_rates_range(symbol, timeframe, start, end)


@router.get("/ticks/{symbol}/from")
def get_ticks_from(symbol: str, date_from: datetime, count: int = 1000, flags: str = "ALL"):
    data = mt5_service.copy_ticks_from(symbol, date_from, count, flags)
    if data is None:
        raise HTTPException(status_code=404, detail="No tick data found")
    return data


@router.get("/ticks/{symbol}/range")
def get_ticks_range(symbol: str, date_from: datetime, date_to: datetime, flags: str = "ALL"):
    data = mt5_service.copy_ticks_range(symbol, date_from, date_to, flags)
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
