from fastapi import APIRouter, HTTPException
from app.services.mt5_service import mt5_service
from typing import Dict, Any

router = APIRouter(prefix="/terminal", tags=["Terminal"])

@router.get("/info")
def get_terminal_info() -> Dict[str, Any]:
    """Get terminal information including broker details."""
    try:
        info = mt5_service.get_terminal_info()
        if info is None:
            raise HTTPException(status_code=500, detail="Failed to get terminal info")
        # Convert MT5 TerminalInfo object to dict
        return {
            "name": info.name,
            "company": info.company,
            "server": info.server,
            "connected": info.connected,
            "language": info.language,
            "path": info.path,
            "community_account": info.community_account,
            "community_connection": info.community_connection,
            "build": info.build,
            "maxbars": info.maxbars,
            "trade_allowed": info.trade_allowed,
            "trade_api": info.trade_api,
            "email_enabled": info.email_enabled,
            "ftp_enabled": info.ftp_enabled,
            "notifications_enabled": info.notifications_enabled,
            "mqid": info.mqid,
            "community_balance": info.community_balance,
            "cpu_cores": info.cpu_cores,
            "cpu_usage": info.cpu_usage,
            "disk_space": info.disk_space,
            "memory_physical": info.memory_physical,
            "memory_total": info.memory_total,
            "memory_available": info.memory_available,
            "memory_used": info.memory_used,
            "ping_last": info.ping_last,
            "last_sync_time": info.last_sync_time,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting terminal info: {str(e)}")

@router.get("/account/info")
def get_account_info() -> Dict[str, Any]:
    """Get account information from MT5 terminal."""
    try:
        account_info = mt5_service.get_account_info()
        if account_info is None:
            raise HTTPException(status_code=500, detail="Failed to get account info")
        # Convert MT5 AccountInfo object to dict
        return {
            "login": account_info.login,
            "trade_mode": account_info.trade_mode,
            "leverage": account_info.leverage,
            "limit_orders": account_info.limit_orders,
            "margin_so_mode": account_info.margin_so_mode,
            "trade_allowed": account_info.trade_allowed,
            "trade_expert": account_info.trade_expert,
            "margin_mode": account_info.margin_mode,
            "currency_digits": account_info.currency_digits,
            "fifo_close": account_info.fifo_close,
            "balance": account_info.balance,
            "credit": account_info.credit,
            "profit": account_info.profit,
            "equity": account_info.equity,
            "margin": account_info.margin,
            "margin_free": account_info.margin_free,
            "margin_level": account_info.margin_level,
            "margin_so_call": account_info.margin_so_call,
            "margin_so_so": account_info.margin_so_so,
            "margin_initial": account_info.margin_initial,
            "margin_maintenance": account_info.margin_maintenance,
            "assets": account_info.assets,
            "liabilities": account_info.liabilities,
            "commission_blocked": account_info.commission_blocked,
            "name": account_info.name,
            "server": account_info.server,
            "currency": account_info.currency,
            "company": account_info.company,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting account info: {str(e)}")