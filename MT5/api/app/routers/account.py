from fastapi import APIRouter
from app.services.mt5_service import mt5_service
from app.services.connector import mt5_connector
from app.services import algo
from app.utils.constants import RETCODE_DESCRIPTIONS
from app.utils.exceptions import MT5ConnectionError

router = APIRouter(prefix="/account", tags=["Account"])


@router.get("/health")
def health():
    """Connected, and whether it could actually trade.

    **`status: healthy` used to mean only that IPC was up.** It read `healthy`
    over a terminal with AutoTrading off, which refuses every order with
    `10027` - so a monitor watching this endpoint reported a working desk
    through an outage in which nothing could be placed. `can_trade` is now part
    of the answer.

    `status` keeps its three existing values and its existing meaning, because
    container healthchecks and deploy gates compare against them: a terminal
    that is connected but not permitted to trade is still `healthy` as far as
    *this process* is concerned. `can_trade` is the separate fact, reported
    beside it rather than folded into a word that already means something else.
    """
    if not mt5_connector._initialized:
        if mt5_connector._initializing:
            return {"status": "connecting", "mt5": "initializing"}
        return {"status": "unhealthy", "mt5": "disconnected"}

    body = {"status": "healthy", "mt5": "connected"}
    # Never let reading the flags fail the healthcheck: a healthcheck that
    # throws is read as "the service is down", which would turn a terminal
    # quirk into a container restart.
    try:
        body.update(algo.tradability())
    except Exception:  # pragma: no cover - defensive
        body["can_trade"] = None
        body["reason"] = "tradability could not be read"
    return body


@router.get("/last_error")
def get_last_error():
    error = mt5_service.last_error()
    return {
        "error_code": error[0],
        "error_message": error[1],
        "description": RETCODE_DESCRIPTIONS.get(error[0], "Unknown error code")
    }


@router.get("/retcodes")
def get_retcodes():
    return RETCODE_DESCRIPTIONS
