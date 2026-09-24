from fastapi import APIRouter, HTTPException, status
from app.services.mt5_service import mt5_service
from app.services.connector import mt5_connector
from app.services import algo
from app.models.trading import AlgoTradingRequest
from app.utils.exceptions import MT5ConnectionError
from typing import Dict, Any
import MetaTrader5 as mt5

router = APIRouter(prefix="/terminal", tags=["Terminal"])


@router.get("/info")
def get_terminal_info() -> Dict[str, Any]:
    info = mt5_service.get_terminal_info()
    if info is None:
        raise MT5ConnectionError("Failed to get terminal info")
    return info._asdict() if hasattr(info, '_asdict') else dict(info)


@router.get("/account/info")
def get_account_info() -> Dict[str, Any]:
    account_info = mt5_service.get_account_info()
    if account_info is None:
        raise MT5ConnectionError("Failed to get account info")
    return account_info._asdict() if hasattr(account_info, '_asdict') else dict(account_info)


@router.get("/version")
def get_mt5_version():
    mt5_service.initialize()
    return {"version": mt5.version()}


@router.post("/disconnect")
def disconnect():
    if not mt5.shutdown():
        raise MT5ConnectionError("Failed to disconnect from MT5 terminal")
    mt5_connector._initialized = False
    return {"status": "disconnected"}


@router.get("/ping")
def ping():
    mt5_service.initialize()
    info = mt5.terminal_info()
    if info is None:
        raise MT5ConnectionError("Terminal not connected")
    return {"ping": info.ping_last}


@router.get("/last_error")
def get_last_error():
    code, msg = mt5.last_error()
    return {"error_code": code, "error_message": msg}


@router.get("/algo-trading")
def get_algo_trading() -> Dict[str, Any]:
    """Whether the terminal will currently accept an order from a program.

    Separate from `/info` - which carries the same field - because this is the
    one thing a deploy step or a monitor wants, and asking for the whole
    terminal record to read one boolean invites reading the wrong boolean.
    `account.trade_allowed` is a *different* flag, set by the broker, and a
    terminal that answers `True` there will still refuse every order while this
    one is `False`.
    """
    allowed = algo.state()
    if allowed is None:
        raise MT5ConnectionError("Failed to read trade_allowed from the terminal")
    return {"trade_allowed": allowed}


@router.post("/algo-trading")
def set_algo_trading(request: AlgoTradingRequest) -> Dict[str, Any]:
    """Put AutoTrading where `enabled` says, and report whether it moved.

    Idempotent: asking for the state the terminal is already in presses nothing
    and returns `changed: false`, so this is safe to call unconditionally on
    every deploy.

    There is no programmatic setter for this flag in `MetaTrader5` - it is a GUI
    switch - so the implementation sends Ctrl+E over VNC. See
    `app.services.algo` for why the state is read first and why that is not an
    optimisation.
    """
    try:
        return algo.apply(request.enabled)
    except algo.AlgoToggleError as exc:
        # 409 rather than 500: the service and the terminal are both fine, and
        # the request was valid. What failed is that the GUI would not move,
        # which is a conflict with the terminal's state and often needs a human.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
