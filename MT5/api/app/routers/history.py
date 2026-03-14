from fastapi import APIRouter, HTTPException
from app.services.mt5_service import mt5_service
from typing import Optional, List
from datetime import datetime
import MetaTrader5 as mt5

router = APIRouter(prefix="/history", tags=["History"])

def error_response(detail: str):
    code, msg = mt5.last_error()
    return HTTPException(status_code=500, detail={"error": detail, "mt5_code": code, "mt5_msg": msg})

@router.get("/deals")
def get_history_deals(from_date: datetime, to_date: datetime, position: Optional[int] = None):
    try:
        return mt5_service.get_history_deals(from_date, to_date, position)
    except Exception as e:
        raise error_response(f"Error fetching deals: {str(e)}")

@router.get("/orders")
def get_history_orders(from_date: Optional[datetime] = None, to_date: Optional[datetime] = None, ticket: Optional[int] = None):
    try:
        return mt5_service.get_history_orders(from_date, to_date, ticket)
    except Exception as e:
        raise error_response(f"Error fetching orders: {str(e)}")

@router.get("/order_by_ticket/{ticket}")
def get_order_by_ticket(ticket: int):
    try:
        orders = mt5.history_orders_get(ticket=ticket)
        if not orders:
            raise HTTPException(status_code=404, detail="Order not found")
        return [o._asdict() for o in orders]
    except Exception as e:
        raise error_response(f"Error fetching order by ticket: {str(e)}")
