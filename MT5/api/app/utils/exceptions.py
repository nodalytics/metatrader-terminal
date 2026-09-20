class MT5BaseException(Exception):
    """Base exception for all MT5 errors."""
    status_code: int = 500

    def __init__(self, message: str, code: int = None):
        self.message = message
        self.code = code
        super().__init__(self.message)

class MT5ConnectionError(MT5BaseException):
    """MT5 terminal is not connected or IPC failed."""
    status_code = 503

class MT5OrderError(MT5BaseException):
    """Order placement, modification, or cancellation failed."""
    status_code = 400

class MT5SymbolNotFoundError(MT5BaseException):
    """Requested symbol does not exist or has no data."""
    status_code = 404

class MT5RateLimitError(MT5BaseException):
    """Too many requests to MT5 terminal."""
    status_code = 429

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
