"""What an `order_send` reply means, in one place.

## The bug this exists to stop repeating

Four routes ended `return {"success": True, "result": result._asdict()}`
without looking at the retcode: `POST /positions/modify`,
`POST /orders/pending`, `PUT /orders/{ticket}` and
`DELETE /orders/{ticket}`. So a client that moved a stop and got
`success: true` had no way to know the terminal refused it, and the field
named `success` was true on every reply the route managed to produce.

`POST /trading/modify-sl-tp` already did it properly - it checks the retcode
and raises `MT5OrderError` - so this is not a new policy. It is the four later
routes being made consistent with the one that was right.

It also crashed on the failure it was ignoring: `mt5_service.modify_sl_tp`
returns `None` when the terminal refuses outright, and `None._asdict()` is an
`AttributeError`, which FastAPI turns into a 500. A refused stop move and a
broken service looked identical from outside.

## Why a helper and not a check per route

Because the check is the same every time and the *accepted codes* are not.
A placement that rests is `TRADE_RETCODE_PLACED`; a close or a cancel is
`TRADE_RETCODE_DONE`; a modify is `DONE`. Written out four times, the
difference between them is a number nobody reviewing a diff would question.
Here it is an argument with a name.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.utils.exceptions import MT5OrderError

try:  # pragma: no cover - present wherever this actually runs
    from app.services.terminal_transport import mt5

    _DONE = mt5.TRADE_RETCODE_DONE
    _PLACED = mt5.TRADE_RETCODE_PLACED
    _last_error = mt5.last_error
except ImportError:
    # **The constants, so this module can be tested off the terminal.**
    # `MetaTrader5` is a Windows package making in-process calls into a
    # running terminal, so it exists in the container and on no development
    # machine - and a hundred lines of retcode logic that cannot be unit
    # tested because of an import is the wrong trade. The values are MQL5's
    # own documented ones and have never moved; the library is still preferred
    # where it is present, which costs nothing and defers to it if they ever
    # do.
    _DONE = 10009
    _PLACED = 10008

    def _last_error():
        return (-1, "MetaTrader5 is not installed in this process")


#: The terminal filled or closed it.
DONE: Tuple[int, ...] = (_DONE,)

#: The terminal accepted it and it is resting. A pending order's success.
#:
#: `DONE` is included because a limit placed at a price the market has already
#: reached fills immediately, and the terminal reports that as `DONE` - which
#: is a successful placement by any reading, and a route that only accepted
#: `PLACED` would call the best possible outcome a failure.
PLACED: Tuple[int, ...] = (_PLACED, _DONE)


def checked(
    result: Optional[Any],
    accept: Tuple[int, ...] = DONE,
    what: str = "request",
) -> Any:
    """The reply, if it was a success. Raises `MT5OrderError` if it was not.

    For the caller that has work to do *after* validating - `POST
    /trading/order` reads the fill price and writes a database row - where
    `answered` would have already built the response. Validating first is the
    point: that route used to read `result.price` immediately, so a refusal
    (`None`) was an `AttributeError` and a 500, and a rejected order still
    **wrote a trade row**, leaving the service's own database carrying
    positions the account never held.
    """
    if result is None:
        code, message = _last_error()
        raise MT5OrderError(f"{what} was not sent: {message}", code=code)
    retcode = int(getattr(result, "retcode", 0) or 0)
    if retcode not in accept:
        raise MT5OrderError(
            f"{what} refused: {getattr(result, 'comment', None) or 'no comment'}",
            code=retcode,
        )
    return result


def answered(
    result: Optional[Any],
    accept: Tuple[int, ...] = DONE,
    what: str = "request",
) -> Dict[str, Any]:
    """The reply as a response body, or `MT5OrderError` if it was a refusal.

    Raising rather than returning `success: false` on purpose. An HTTP client
    has to handle a 4xx; a 200 carrying `success: false` is only handled by a
    client that remembered to look, and the whole reason this module exists is
    that one did not.

    The retcode and the terminal's own comment go into the error, because
    "Modify failed" sends a reader to the logs while "Invalid stops (10016)"
    tells them what to change.
    """
    # `order_send` returns `None` when the request never reached the server -
    # a terminal that is not connected, or a request the library rejected -
    # and `last_error` is the only description of it. Delegated so the two
    # entry points cannot come to disagree about what a refusal is.
    result = checked(result, accept, what)
    body = result._asdict() if hasattr(result, "_asdict") else dict(result)
    # `success` stays in the shape, because clients read it. It is now only
    # ever `true`, since a refusal raises - which is what the name always
    # implied and never meant.
    return {"success": True, "result": body}
