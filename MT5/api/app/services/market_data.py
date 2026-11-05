from app.services.terminal_transport import mt5
import pandas as pd
import time
from datetime import datetime
from typing import Optional, List, Dict
from .connector import mt5_connector
from app.utils.constants import MT5Timeframe
from app.utils.exceptions import MT5DataError, MT5SymbolNotFoundError
from app.utils.cache import cache_manager

#: The terminal's own spelling, keyed by upper case.
#:
#: Symbol names in MT5 are **case-sensitive and often not upper case**:
#: `Germany 40`, `Wall Street 30`, `US Tech 100`, and every Deriv synthetic
#: (`Volatility 75 Index`, `Boom 1000 Index`).
_BY_UPPER: Dict[str, str] = {}

#: When the map was last built, and how stale it may get. `None` is never
#: built, and must not be spelled `0.0`: `time.monotonic()` counts from boot,
#: so on a machine whose uptime is under a minute `monotonic() - 0.0` is not
#: greater than the window and the map would never be built at all. That is
#: not hypothetical - it passed on a developer machine and failed in CI, whose
#: containers are seconds old.
#:
#: **Rebuilding on every miss is a round trip per unknown name.** The first
#: version did, so a batch of fifty names the terminal does not have became
#: fifty full `symbols_get()` calls over IPC - which a caller chooses the size
#: of. One rebuild a minute bounds that to the same cost regardless, and a
#: symbol listed since the last build is still found within the minute.
_BUILT_AT: float | None = None
REBUILD_AFTER_S: float = 60.0


def resolve_symbol(name: str) -> str:
    """The terminal's spelling of `name`, or `name` when it knows no such symbol.

    Callers used to be normalised with `.upper()`, which is invisible on
    `EURUSD` and `XAUUSD` and makes every mixed-case symbol unresolvable - the
    terminal answers `Tick data for 'GERMANY 40' not found` for a symbol it
    holds as `Germany 40`. That survived as long as the desk traded eight FX
    pairs and two metals and broke the moment indices were added.

    Resolving rather than keeping the caller's spelling verbatim, because
    `xauusd` resolving to `XAUUSD` is a contract this API already has and is
    worth keeping: a caller should not have to know the terminal's casing.

    The exact name is tried first, so a symbol that exists costs one lookup and
    no map. The map is built only when a name does not resolve, and rebuilt on
    a miss so a symbol added since the last build is found.
    """
    global _BUILT_AT

    wanted = str(name or "").strip()
    if not wanted:
        return wanted
    if mt5.symbol_info(wanted) is not None:
        return wanted
    key = wanted.upper()
    found = _BY_UPPER.get(key)
    stale = _BUILT_AT is None or (time.monotonic() - _BUILT_AT) > REBUILD_AFTER_S
    if found is None and stale:
        fresh: Dict[str, str] = {}
        for info in (mt5.symbols_get() or ()):
            actual = getattr(info, "name", None)
            if actual:
                fresh[str(actual).upper()] = str(actual)
        # Swapped rather than cleared and refilled, so a concurrent reader
        # never sees a half-built map.
        _BY_UPPER.clear()
        _BY_UPPER.update(fresh)
        _BUILT_AT = time.monotonic()
        found = _BY_UPPER.get(key)
    return found or wanted


def forget_symbols() -> None:
    """Drop the cached spellings so the next lookup rebuilds.

    The map is module state with a one-minute staleness window, which is right
    for a terminal whose symbol list changes rarely and wrong for a test whose
    terminal changes every function - without this, a symbol added by one test
    is invisible to the next for as long as the suite takes to run.
    """
    global _BUILT_AT

    _BY_UPPER.clear()
    _BUILT_AT = None


def resolve_symbols(names: List[str]) -> List[str]:
    """`resolve_symbol` over a list, for one hop into the thread pool.

    The callers are async handlers and every blocking terminal call in them
    goes through `run_in_threadpool`; resolving one name at a time would make
    that one hop per name.
    """
    return [resolve_symbol(name) for name in names]


class MarketDataService:
    def get_symbols(self) -> List[str]:
        cache_key = "all_symbols_list"
        cached_symbols = cache_manager.get(cache_key)
        if cached_symbols:
            return cached_symbols

        mt5_connector.initialize()
        symbols = mt5.symbols_get()
        if not symbols:
            return []
            
        symbols_list = [s.name for s in symbols]
        cache_manager.set(cache_key, symbols_list, ttl=3600)
        return symbols_list

    def get_timeframe(self, timeframe_str: str) -> int:
        try:
            return MT5Timeframe[timeframe_str.upper()].value
        except KeyError:
            valid_timeframes = ', '.join([t.name for t in MT5Timeframe])
            raise ValueError(f"Invalid timeframe: '{timeframe_str}'. Valid options are: {valid_timeframes}.")

    def get_symbol_info(self, symbol: str) -> Dict:
        cache_key = f"symbol_info_{symbol}"
        cached_info = cache_manager.get(cache_key)
        if cached_info:
            return cached_info

        mt5_connector.initialize()
        info = mt5.symbol_info(symbol)
        if not info:
            raise MT5SymbolNotFoundError(f"Symbol '{symbol}' not found.")
        
        info_dict = info._asdict()
        cache_manager.set(cache_key, info_dict, ttl=300)  # Symbol info changes rarely
        return info_dict

    def select_symbol(self, symbol: str) -> bool:
        mt5_connector.initialize()
        return mt5.symbol_select(symbol, True)

    def get_symbol_info_tick(self, symbol: str, use_cache: bool = True) -> Dict:
        """The latest quote. `use_cache=False` for anything streaming.

        The cache holds a tick for a second, which is right for a REST caller
        polling and wrong for a stream: a socket sampling four times a second
        would send the same cached tick four times and miss three real ones.
        """
        cache_key = f"symbol_tick_{symbol}"
        if use_cache:
            cached_tick = cache_manager.get(cache_key)
            if cached_tick:
                return cached_tick

        mt5_connector.initialize()
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            raise MT5SymbolNotFoundError(f"Tick data for '{symbol}' not found.")

        tick_dict = tick._asdict()
        cache_manager.set(cache_key, tick_dict, ttl=1)  # Tick data changes frequently
        return tick_dict


    def _records(self, rates, symbol: str, what: str) -> List[Dict]:
        """Bars as JSON records, keeping an empty market apart from a failure.

        Two distinct answers used to collapse into one 404:

        * `None` - the terminal failed. Raised, with MT5's own error attached by
          the application's exception handler, so a caller can see *why*.
        * empty - a real answer about a window with no bars in it. Returned as
          `[]`, which it previously could not be: `pd.DataFrame([])` has no
          `time` column, so the next line raised `KeyError` and the route 500d.
        """
        if rates is None:
            raise MT5DataError(f"{what} failed for '{symbol}'")
        if len(rates) == 0:
            return []
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df.to_dict(orient='records')

    def copy_rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int) -> List[Dict]:
        mt5_connector.initialize()
        mt5_timeframe = self.get_timeframe(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, start_pos, count)
        return self._records(rates, symbol, 'copy_rates_from_pos')

    def copy_rates_range(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> List[Dict]:
        mt5_connector.initialize()
        mt5_timeframe = self.get_timeframe(timeframe)
        rates = mt5.copy_rates_range(symbol, mt5_timeframe, start, end)
        return self._records(rates, symbol, 'copy_rates_range')

    def copy_rates_from(self, symbol: str, timeframe: str, date_from: datetime, count: int) -> List[Dict]:
        mt5_connector.initialize()
        mt5.symbol_select(symbol, True)
        mt5_timeframe = self.get_timeframe(timeframe)
        rates = mt5.copy_rates_from(symbol, mt5_timeframe, date_from, count)
        return self._records(rates, symbol, 'copy_rates_from')

    def copy_ticks_from(self, symbol: str, date_from: datetime, count: int, flags: str = 'ALL') -> Optional[List[Dict]]:
        mt5_connector.initialize()
        mt5.symbol_select(symbol, True)
        flags_map = {
            'ALL': mt5.COPY_TICKS_ALL,
            'INFO': mt5.COPY_TICKS_INFO,
            'TRADE': mt5.COPY_TICKS_TRADE,
        }
        ticks = mt5.copy_ticks_from(symbol, date_from, count, flags_map.get(flags.upper(), mt5.COPY_TICKS_ALL))
        if ticks is None or len(ticks) == 0: return None
        df = pd.DataFrame(ticks)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df['time_msc'] = pd.to_datetime(df['time_msc'], unit='ms')
        return df.to_dict(orient='records')

    def copy_ticks_range(self, symbol: str, date_from: datetime, date_to: datetime, flags: str = 'ALL') -> Optional[List[Dict]]:
        mt5_connector.initialize()
        mt5.symbol_select(symbol, True)
        flags_map = {
            'ALL': mt5.COPY_TICKS_ALL,
            'INFO': mt5.COPY_TICKS_INFO,
            'TRADE': mt5.COPY_TICKS_TRADE,
        }
        ticks = mt5.copy_ticks_range(symbol, date_from, date_to, flags_map.get(flags.upper(), mt5.COPY_TICKS_ALL))
        if ticks is None or len(ticks) == 0: return None
        df = pd.DataFrame(ticks)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df['time_msc'] = pd.to_datetime(df['time_msc'], unit='ms')
        return df.to_dict(orient='records')

market_data_service = MarketDataService()
