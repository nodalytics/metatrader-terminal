from fastapi import APIRouter, HTTPException, status
from app.services.mt5_service import mt5_service
from app.models.trading import PendingOrderRequest, ModifyPendingOrderRequest
from app.utils.sent import DONE, PLACED, answered
from typing import Optional
from app.services.terminal_transport import mt5

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.get("/")
def get_orders(symbol: Optional[str] = None, ticket: Optional[int] = None):
    return mt5_service.get_orders(symbol=symbol, ticket=ticket)


@router.get("/total")
def get_orders_total():
    return {"total": mt5_service.get_orders_total()}


@router.post("/pending", status_code=status.HTTP_201_CREATED)
def send_pending_order(request: PendingOrderRequest):
    result = mt5_service.send_pending_order(
        symbol=request.symbol,
        volume=request.volume,
        order_type=request.order_type,
        price=request.price,
        sl=request.sl,
        tp=request.tp,
        deviation=request.deviation,
        comment=request.comment,
        magic=request.magic,
        type_filling=request.type_filling,
        type_time=request.type_time,
        expiration=request.expiration,
        stoplimit=request.stoplimit,
    )
    # `PLACED` and not `DONE`: a pending order's success is that it rests.
    # `DONE` is accepted too, because a limit at a price the market has
    # already reached fills immediately - the best available outcome, which a
    # `PLACED`-only check would report as a failure. See `utils/sent.py`.
    return answered(result, PLACED, f"pending {request.order_type} {request.symbol}")


@router.put("/{ticket}")
def modify_pending_order(ticket: int, request: ModifyPendingOrderRequest):
    result = mt5_service.modify_pending_order(
        ticket=ticket,
        price=request.price,
        sl=request.sl,
        tp=request.tp,
        type_time=request.type_time,
        expiration=request.expiration,
    )
    return answered(result, DONE, f"modify of order {ticket}")


@router.delete("/{ticket}")
def cancel_order(ticket: int):
    result = mt5_service.cancel_order(ticket)
    # A cancel that the terminal refused used to answer `success: true`, so a
    # client would report an order gone while it was still working - and then
    # be surprised by its fill.
    return answered(result, DONE, f"cancel of order {ticket}")


@router.get("/calc/margin")
def calc_margin(action: str, symbol: str, volume: float, price: float):
    result = mt5_service.order_calc_margin(action, symbol, volume, price)
    if result is None:
        raise HTTPException(status_code=400, detail="Failed to calculate margin")
    return {"margin": result}


@router.get("/calc/profit")
def calc_profit(action: str, symbol: str, volume: float, price_open: float, price_close: float):
    result = mt5_service.order_calc_profit(action, symbol, volume, price_open, price_close)
    if result is None:
        raise HTTPException(status_code=400, detail="Failed to calculate profit")
    return {"profit": result}
