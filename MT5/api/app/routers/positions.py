from fastapi import APIRouter
from app.services.mt5_service import mt5_service
from app.models.trading import ModifySLTPRequest
from app.utils.exceptions import MT5SymbolNotFoundError
from typing import Optional
import MetaTrader5 as mt5

router = APIRouter(prefix="/positions", tags=["Positions"])


@router.get("/")
def get_positions(magic: Optional[int] = None):
    return mt5_service.get_positions(magic)


@router.post("/close")
def close_position(ticket: int, volume: Optional[float] = None, type_filling: str = "FOK"):
    result = mt5_service.close_position(ticket, volume=volume, type_filling=type_filling)
    return {"success": True, "result": result._asdict()}


@router.post("/close_all")
def close_all_positions(order_type: str = "all", magic: Optional[int] = None, type_filling: str = "FOK"):
    results = mt5_service.close_all_positions(order_type, magic, type_filling=type_filling)
    return {"message": f"Closed {len(results)} positions", "results": [r._asdict() for r in results]}


@router.post("/modify")
def modify_position(request: ModifySLTPRequest):
    """Move a position's stop or target, addressed by its **ticket**.

    The existing route for this lives under `/trading/modify-sl-tp` and takes a
    `trade_id` — a row in this service's own database — which it uses only to
    look the ticket up. That makes a position unmodifiable unless this service
    happens to have recorded it, which excludes every position opened before a
    redeploy, every one placed through the terminal directly, and every one
    from a client that did not keep the row it was handed back.

    A ticket is what MT5 itself uses and what every other route here takes, so
    a trailing stop needs nothing but this. The older route stays for callers
    that already use it.
    """
    result = mt5_service.modify_sl_tp(request.ticket, request.sl, request.tp)
    return {"success": True, "result": result._asdict()}


@router.get("/by_symbol/{symbol}")
def get_positions_by_symbol(symbol: str):
    mt5_service.initialize()
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        raise MT5SymbolNotFoundError(f"No positions found for symbol '{symbol}'")
    return [p._asdict() for p in positions]
