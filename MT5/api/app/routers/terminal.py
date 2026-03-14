from fastapi import APIRouter, HTTPException
from app.services.mt5_service import mt5_service
from typing import Dict, Any
import MetaTrader5 as mt5

router = APIRouter(prefix="/terminal", tags=["Terminal"])

def error_response(detail: str):
    code, msg = mt5.last_error()
    return HTTPException(status_code=500, detail={"error": detail, "mt5_code": code, "mt5_msg": msg})

@router.get("/info")
def get_terminal_info() -> Dict[str, Any]:
    info = mt5_service.get_terminal_info()
    if info is None:
        raise error_response("Failed to get terminal info")
    return info._asdict() if hasattr(info, '_asdict') else dict(info)

@router.get("/account/info")
def get_account_info() -> Dict[str, Any]:
    account_info = mt5_service.get_account_info()
    if account_info is None:
        raise error_response("Failed to get account info")
    return account_info._asdict() if hasattr(account_info, '_asdict') else dict(account_info)

@router.get("/version")
def get_mt5_version():
    try:
        return {"version": mt5.version()}
    except Exception as e:
        raise error_response(f"Error getting MT5 version: {str(e)}")

@router.post("/connect")
def connect(login: int, password: str, server: str):
    try:
        if not mt5.initialize(login=login, password=password, server=server):
            raise error_response("Failed to connect to MT5 terminal")
        return {"status": "connected"}
    except Exception as e:
        raise error_response(f"Error connecting: {str(e)}")

@router.post("/disconnect")
def disconnect():
    try:
        if not mt5.shutdown():
            raise error_response("Failed to disconnect from MT5 terminal")
        return {"status": "disconnected"}
    except Exception as e:
        raise error_response(f"Error disconnecting: {str(e)}")

@router.get("/ping")
def ping():
    try:
        return {"ping": mt5.terminal_info().ping_last}
    except Exception as e:
        raise error_response(f"Error pinging: {str(e)}")

@router.get("/last_error")
def get_last_error():
    code, msg = mt5.last_error()
    return {"error_code": code, "error_message": msg}