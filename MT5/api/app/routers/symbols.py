from fastapi import APIRouter, HTTPException
from app.services.mt5_service import mt5_service
from typing import Optional, List
from datetime import datetime
import MetaTrader5 as mt5

router = APIRouter(prefix="/symbols", tags=["Symbols"])

def error_response(detail: str):
    code, msg = mt5.last_error()
    return HTTPException(status_code=500, detail={"error": detail, "mt5_code": code, "mt5_msg": msg})

@router.get("/", response_model=List[str])
def get_all_symbols():
    try:
        return mt5_service.get_symbols()
    except Exception as e:
        raise error_response(f"Error fetching symbols: {str(e)}")

@router.get("/{symbol}")
def get_symbol(symbol: str):
    try:
        info = mt5_service.get_symbol_info(symbol)
        if not info:
            raise HTTPException(status_code=404, detail="Symbol not found")
        return info
    except Exception as e:
        raise error_response(f"Error fetching symbol: {str(e)}")

@router.get("/ticks/{symbol}")
def get_symbol_tick(symbol: str):
    try:
        return mt5_service.get_symbol_info_tick(symbol)
    except Exception as e:
        raise error_response(f"Error fetching tick: {str(e)}")


# Support both /info/{symbol} and /info?symbol=...
from fastapi import Query

@router.get("/info/{symbol}")
def get_symbol_info_path(symbol: str):
    try:
        info = mt5_service.get_symbol_info(symbol)
        if not info:
            return {"error": "Symbol not found", "symbol": symbol}
        return info
    except Exception as e:
        raise error_response(f"Error fetching symbol info: {str(e)}")

@router.get("/info")
def get_symbol_info_query(symbol: str = Query(...)):
    try:
        info = mt5_service.get_symbol_info(symbol)
        if not info:
            return {"error": "Symbol not found", "symbol": symbol}
        return info
    except Exception as e:
        raise error_response(f"Error fetching symbol info: {str(e)}")

@router.get("/rates/pos")
def fetch_data_pos(symbol: str, timeframe: str = "M1", num_bars: int = 100):
    try:
        return mt5_service.copy_rates_from_pos(symbol, timeframe, 0, num_bars)
    except Exception as e:
        raise error_response(f"Error fetching rates: {str(e)}")

@router.get("/rates/range")
def fetch_data_range(symbol: str, timeframe: str, start: datetime, end: datetime):
    try:
        data = mt5_service.copy_rates_range(symbol, timeframe, start, end)
        if data is None:
            raise HTTPException(status_code=404, detail="Failed to fetch data")
        return data
    except Exception as e:
        raise error_response(f"Error fetching rates range: {str(e)}")

@router.get("/book/{symbol}")
def get_book(symbol: str):
    try:
        book = mt5.market_book_get(symbol)
        if book is None:
            raise HTTPException(status_code=404, detail="No book data")
        return [b._asdict() for b in book]
    except Exception as e:
        raise error_response(f"Error fetching book: {str(e)}")

@router.get("/check/{symbol}")
def check_symbol(symbol: str):
    try:
        info = mt5.symbol_info(symbol)
        if not info:
            raise HTTPException(status_code=404, detail="Symbol not found")
        return {"visible": info.visible, "select": info.select, "name": info.name}
    except Exception as e:
        raise error_response(f"Error checking symbol: {str(e)}")
