from app.utils.constants import RETRYABLE_RETCODES


class MT5BaseException(Exception):
    """Base exception for all MT5 errors.

    **`kind` exists because the status code is not enough to branch on.** A
    broker refusing a trade and a bad API key are both 4xx, and a client that
    wanted to tell them apart had to match on the text of `error` - so one
    client reported a broken deployment as an ordinary bad trade. `kind` is a
    closed set a client can switch on and that does not change when a message
    is reworded.
    """

    status_code: int = 500
    #: One of `KINDS`. The subclass's answer to "what sort of thing went
    #: wrong", which is the question a client is really asking.
    kind: str = "internal"
    #: Whether sending the *same request* again could succeed. A class-level
    #: default; `MT5OrderError` decides per retcode, because "the broker said
    #: no" covers both a requote and not having the money.
    retryable: bool = False

    def __init__(self, message: str, code: int = None):
        self.message = message
        self.code = code
        super().__init__(self.message)

    def is_retryable(self) -> bool:
        """Whether the same request could succeed if sent again."""
        return self.retryable


#: Every value `kind` can take, so a client can exhaust it.
#:
#: - `validation` - the request was malformed and the terminal never saw it.
#:   Fix the request.
#: - `refusal`    - the terminal processed the request and the answer was no.
#:   There is a retcode. `retryable` says whether sending it again is sensible.
#: - `terminal`   - the terminal or its trade server is unreachable or failed.
#:   The request may be fine.
#: - `not_found`  - the symbol, order or position does not exist.
#: - `rate_limit` - too many requests to the terminal.
#: - `auth`       - the caller is not authenticated for this API.
#: - `internal`   - a defect in this service.
KINDS = ("validation", "refusal", "terminal", "not_found", "rate_limit", "auth", "internal")

class MT5ConnectionError(MT5BaseException):
    """MT5 terminal is not connected or IPC failed."""
    status_code = 503
    kind = "terminal"
    #: The request itself was never judged, so it may well be fine.
    retryable = True

class MT5OrderError(MT5BaseException):
    """Order placement, modification, or cancellation failed."""
    status_code = 400
    kind = "refusal"

    def is_retryable(self) -> bool:
        """Per retcode, not per class.

        "The broker said no" covers a requote, which clears in a second, and
        not having the money, which does not. A client retrying all of them
        loops forever on the second; one retrying none gives up on the first.
        """
        return self.code in RETRYABLE_RETCODES

class MT5ValidationError(MT5BaseException):
    """The request is ruled out by the symbol's own specification.

    **A 422 and not a 400, because the terminal never saw it.** A volume off
    the symbol's step used to reach MT5 and come back `10014 Invalid volume` -
    a refusal, retryable-looking, and silent about which of volume_min,
    volume_max or volume_step it broke. This says which, and `kind` says it is
    the request that is wrong rather than the market.
    """

    status_code = 422
    kind = "validation"


class MT5SymbolNotFoundError(MT5BaseException):
    """Requested symbol does not exist or has no data."""
    status_code = 404
    kind = "not_found"

class MT5RateLimitError(MT5BaseException):
    """Too many requests to MT5 terminal."""
    status_code = 429
    kind = "rate_limit"
    retryable = True

class MT5DataError(MT5BaseException):
    """The terminal refused or failed a data request.

    **Not a 404.** `copy_rates_*` and `copy_ticks_*` answer `None` both for a
    symbol that does not exist and for a request the terminal could not
    marshal - a count too large for it, an IPC hiccup, history still loading.
    Reporting all of those as "not found" told one client that all 722 of its
    symbols were absent when the truth was that it had asked for 400,000 bars.
    502 says the upstream failed, and the handler attaches MT5's own error.
    """
    status_code = 502
    kind = "terminal"
    retryable = True
