from fastapi import APIRouter, HTTPException
from app.services.mt5_service import mt5_service
from app.utils.constants import RETCODE_DESCRIPTIONS
import MetaTrader5 as mt5

router = APIRouter(prefix="/account", tags=["Account"])

def error_response(detail: str):
    code, msg = mt5.last_error()
    return HTTPException(status_code=500, detail={"error": detail, "mt5_code": code, "mt5_msg": msg})

@router.get("/health")
def health():
    try:
        if mt5_service.initialize():
            return {"status": "healthy", "mt5": "connected"}
        return {"status": "unhealthy", "mt5": "disconnected"}
    except Exception as e:
        raise error_response(f"Error checking health: {str(e)}")

@router.get("/last_error")
def get_last_error():
    try:
        error = mt5_service.last_error()
        return {
            "error_code": error[0],
            "error_message": error[1],
            "description": RETCODE_DESCRIPTIONS.get(error[0], "Unknown error code")
        }
    except Exception as e:
        raise error_response(f"Error getting last error: {str(e)}")

@router.get("/retcodes")
def get_retcodes():
    return RETCODE_DESCRIPTIONS
