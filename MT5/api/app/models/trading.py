
from typing import Optional, List, Any
from pydantic import BaseModel, Field, root_validator
from app.utils.constants import (
    RETCODE_DESCRIPTIONS, 
    ORDER_TYPE_STR_MAP, 
    ORDER_FILLING_STR_MAP
)

class MarketOrderRequest(BaseModel):
    symbol: str
    volume: float
    order_type: str = Field(..., pattern="^(BUY|SELL)$")
    sl: float
    tp: Optional[float] = None
    deviation: int = 20
    comment: str = ""
    magic: int = 0
    type_filling: str = "IOC"

class PendingOrderRequest(BaseModel):
    symbol: str
    volume: float
    order_type: str # 'BUY_LIMIT', 'SELL_LIMIT', etc.
    price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    deviation: Optional[int] = 20
    comment: Optional[str] = ""
    magic: Optional[int] = 0

class ModifySLTPRequest(BaseModel):
    ticket: int
    sl: float
    tp: Optional[float] = None

class PositionInfo(BaseModel):
    ticket: int
    time: int
    time_msc: int
    time_update: int
    time_update_msc: int
    type: int
    magic: int
    identifier: int
    reason: int
    volume: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    swap: float
    profit: float
    symbol: str
    comment: str
    external_id: str

    type_str: str = ""

    @root_validator(pre=False)
    def compute_type_str(cls, values):
        t = values.get('type')
        if t is not None:
            values['type_str'] = ORDER_TYPE_STR_MAP.get(t, f"UNKNOWN({t})")
        return values

class TradeResponse(BaseModel):
    retcode: int
    order: int
    volume: float
    price: float
    comment: str

    retcode_str: str = ""

    @root_validator(pre=False)
    def compute_retcode_str(cls, values):
        r = values.get('retcode')
        if r is not None:
            values['retcode_str'] = RETCODE_DESCRIPTIONS.get(r, f"UNKNOWN({r})")
        return values

class ClosePositionRequest(BaseModel):
    ticket: int
    volume: Optional[float] = None
