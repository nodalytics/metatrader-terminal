import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
from typing import Optional, List, Dict
from .connector import mt5_connector
from app.utils.constants import MT5Timeframe
from app.utils.exceptions import MT5SymbolNotFoundError
from app.utils.cache import cache_manager

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

    def copy_rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int) -> Optional[List[Dict]]:
        mt5_connector.initialize()
        mt5_timeframe = self.get_timeframe(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, start_pos, count)
        if rates is None: return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df.to_dict(orient='records')

    def copy_rates_range(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> Optional[List[Dict]]:
        mt5_connector.initialize()
        mt5_timeframe = self.get_timeframe(timeframe)
        rates = mt5.copy_rates_range(symbol, mt5_timeframe, start, end)
        if rates is None: return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df.to_dict(orient='records')

    def copy_rates_from(self, symbol: str, timeframe: str, date_from: datetime, count: int) -> Optional[List[Dict]]:
        mt5_connector.initialize()
        mt5.symbol_select(symbol, True)
        mt5_timeframe = self.get_timeframe(timeframe)
        rates = mt5.copy_rates_from(symbol, mt5_timeframe, date_from, count)
        if rates is None: return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df.to_dict(orient='records')

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
