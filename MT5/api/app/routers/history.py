from fastapi import APIRouter, HTTPException, Query
from app.services.mt5_service import mt5_service
from typing import Optional
from datetime import datetime
from app.services.terminal_transport import mt5

router = APIRouter(prefix="/history", tags=["History"])


@router.get("/deals")
def get_history_deals(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    date_from: Optional[datetime] = Query(None, include_in_schema=False),
    date_to: Optional[datetime] = Query(None, include_in_schema=False),
    position: Optional[int] = None,
):
    """Deals in a window. **Both spellings of the range are accepted.**

    This service spelled the same concept two ways: `/symbols/ticks/{s}/range`
    and `/symbols/rates/from` declare `date_from`, these two routes declared
    `from_date`. FastAPI drops an unrecognised query parameter silently, and
    `HistoryService` then falls back to its own 30-day window - so a client
    that used the symbols spelling here got a month and was told it was an
    hour.

    Measured against a live terminal on 2026-11-04, asking for one hour:
    `from_date`/`to_date` returned **0** deals, which was correct;
    `date_from`/`date_to` returned **4,013**, spanning 30 days. Nothing
    errored, and the 30-day payload is about 1MB.

    The aliases are `include_in_schema=False` because `from_date` remains the
    documented name - this is here so that neither caller is wrong, not to
    bless two names. An explicit `from_date` wins if both are sent.
    """
    return mt5_service.get_history_deals(from_date or date_from, to_date or date_to, position)


@router.get("/orders")
def get_history_orders(
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    date_from: Optional[datetime] = Query(None, include_in_schema=False),
    date_to: Optional[datetime] = Query(None, include_in_schema=False),
    ticket: Optional[int] = None,
):
    """Orders in a window, or one by ticket. Both range spellings, as above.

    The unbounded case is worth avoiding for a second reason: with the 30-day
    fallback this route returned about 1MB and came back **unparseable** once -
    `Expecting ',' delimiter` at roughly a megabyte in. Bounded windows have
    not reproduced it.
    """
    return mt5_service.get_history_orders(from_date or date_from, to_date or date_to, ticket)


@router.get("/order_by_ticket/{ticket}")
def get_order_by_ticket(ticket: int):
    mt5_service.initialize()
    orders = mt5.history_orders_get(ticket=ticket)
    if not orders:
        raise HTTPException(status_code=404, detail=f"Order {ticket} not found")
    return [o._asdict() for o in orders]
