import MetaTrader5 as mt5
import logging
import os
import threading
import time
from app.utils.exceptions import MT5ConnectionError
from app.utils.config import settings

logger = logging.getLogger(__name__)

LOGIN_MARKER = "/tmp/login_complete"
MT5_PATH = "C:\\Metatrader-5\\terminal64.exe"
MAX_IPC_RETRIES = 1

#: MT5 result codes that restarting cannot fix.
#:
#: The restart exists for a wedged IPC pipe, where a fresh wineserver is
#: genuinely the cure. These are not that. `-6` is the terminal rejecting the
#: credentials and `-8` is algo trading being switched off; both will fail
#: exactly the same way after a restart, so exiting on them turns a
#: configuration mistake into a restart loop.
#:
#: The cost of getting this wrong is not just the loop. `os._exit` kills the
#: process mid-response, so a client asking any MT5-backed route gets
#: "connection reset by peer" — which says nothing at all — where the terminal
#: had in fact given a precise reason. Measured against a live Deriv demo with
#: a wrong login: MT5's own log said "authorization on Deriv-Demo failed
#: (Invalid account)" while every HTTP call died with a reset.
#: How long a fatal refusal is believed before the terminal is asked again.
#:
#: Without this the refusal latches: `_last_error` is set, `_initialized` stays
#: False, and `initialize` reports the same failure forever — including after
#: an operator has fixed the login in the GUI, which is exactly what they will
#: do next. The container would then need a restart to notice it was working,
#: which is the behaviour this whole change set out to remove.
#:
#: A minute is long enough that a wrong password is not retried in a hot loop,
#: and short enough that fixing it by hand feels like it worked.
FATAL_RETRY_AFTER = 60.0

FATAL_CODES = {
    -6,  # RES_E_AUTH_FAILED — wrong login, password or server
    -8,  # RES_E_AUTO_TRADING_DISABLED
    -5,  # RES_E_INVALID_VERSION
    -2,  # RES_E_INVALID_PARAMS
}


def restart_helps(error_code: int) -> bool:
    """Whether bouncing the process could plausibly fix this failure."""
    return int(error_code) not in FATAL_CODES


class MT5Connector:
    """MT5 connection manager.

    Waits for auto-login to create LOGIN_MARKER (VNC login done),
    then calls mt5.initialize() with credentials in a background
    thread to establish the IPC pipe without blocking uvicorn.

    If IPC fails after MAX_IPC_RETRIES attempts, exits the process
    so supervisor can restart the server with a fresh wineserver.
    """

    def __init__(self):
        self._initialized = False
        self._initializing = False
        self._ipc_failures = 0
        #: The terminal's own last refusal, so the API can quote it rather
        #: than saying "still connecting" forever, and when it was given.
        self._last_error = None
        self._last_error_at = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _login_ready() -> bool:
        return os.path.exists(LOGIN_MARKER)

    def _do_initialize(self):
        """Blocking init — runs in a background thread after marker exists."""
        try:
            login = settings.env.MT5_LOGIN
            password = settings.env.MT5_PASSWORD
            server = settings.env.MT5_SERVER

            # Try without credentials first — auto-login already handled
            # the VNC login, so passing credentials again would trigger an
            # "account changed" event that disables algo trading.
            logger.info(f"MT5 initialization started (login={login}, server={server})...")
            success = mt5.initialize(MT5_PATH, portable=True)

            if not success:
                # Fallback: pass credentials (needed on first-ever start
                # before auto-login config is saved by the terminal).
                logger.info("Retrying with credentials...")
                success = mt5.initialize(
                    MT5_PATH,
                    login=login,
                    password=password,
                    server=server,
                    portable=True,
                )

            if success:
                self._initialized = True
                self._ipc_failures = 0
                logger.info("MT5 initialized successfully")

                # Check algo trading status (informational only —
                # auto-login enables it via VNC Ctrl+E)
                info = mt5.terminal_info()
                if info and not info.trade_allowed:
                    logger.warning(
                        "Algo trading is currently disabled — "
                        "trading requests will fail until it is enabled"
                    )
                else:
                    logger.info(f"Algo trading: {'enabled' if info and info.trade_allowed else 'unknown'}")
            else:
                error_code, error_msg = mt5.last_error()
                self._ipc_failures += 1
                logger.error(
                    f"MT5 initialization failed ({self._ipc_failures}/{MAX_IPC_RETRIES}): "
                    f"{error_msg} ({error_code})"
                )
                self._last_error = (error_code, error_msg)
                self._last_error_at = time.monotonic()

                if not restart_helps(error_code):
                    # A restart cannot fix a rejected login or disabled algo
                    # trading. Staying up means the API can answer with the
                    # reason instead of dying mid-response and leaving the
                    # caller with a reset socket.
                    logger.critical(
                        f"MT5 refused the connection: {error_msg} ({error_code}). "
                        "Restarting cannot fix this — check MT5_LOGIN, MT5_PASSWORD "
                        "and MT5_SERVER, and that algo trading is enabled. "
                        "The API stays up and will report this on every request."
                    )
                    return

                if self._ipc_failures >= MAX_IPC_RETRIES:
                    logger.critical(
                        f"MT5 IPC failed {MAX_IPC_RETRIES} times — "
                        "exiting server for fresh wineserver restart..."
                    )
                    # os._exit bypasses try/except — supervisor restarts
                    # both the server and MT5 (autorestart=true)
                    os._exit(1)
        except Exception as e:
            logger.exception(f"MT5 initialization crashed: {e}")
        finally:
            self._initializing = False

    def _start_init(self):
        """Kick off background init if marker exists and not already running."""
        with self._lock:
            if self._initialized or self._initializing:
                return
            if not self._login_ready():
                return
            self._initializing = True
            thread = threading.Thread(target=self._do_initialize, daemon=True)
            thread.start()

    def initialize(self) -> bool:
        """Ensure MT5 is connected. Returns True or raises MT5ConnectionError."""
        if self._initialized:
            return True

        if self._initializing:
            raise MT5ConnectionError(
                "MT5 is still connecting — try again shortly"
            )

        if not self._login_ready():
            raise MT5ConnectionError(
                "Waiting for MT5 auto-login to complete"
            )

        # A refusal the terminal has already given, quoted back. Without this
        # the caller is told "initialization started — try again shortly"
        # forever, while the actual answer ("Invalid account") sits in a log
        # file inside the container.
        #
        # Held for FATAL_RETRY_AFTER and then dropped, so that fixing the login
        # in the GUI — the obvious response to this message — is noticed
        # without a restart. Latching it permanently would have replaced one
        # unrecoverable state with another.
        if self._last_error and not restart_helps(self._last_error[0]):
            if time.monotonic() - self._last_error_at < FATAL_RETRY_AFTER:
                code, message = self._last_error
                raise MT5ConnectionError(
                    f"MT5 refused the connection: {message} ({code}). "
                    "Check MT5_LOGIN, MT5_PASSWORD and MT5_SERVER.",
                    code=code,
                )
            logger.info("Retrying the MT5 connection after an earlier refusal.")
            self._last_error = None
            self._ipc_failures = 0

        # Marker exists but not yet initialized — kick off background init
        self._start_init()
        raise MT5ConnectionError(
            "MT5 initialization started — try again shortly"
        )

    def get_terminal_info(self):
        self.initialize()
        return mt5.terminal_info()

    def get_account_info(self):
        self.initialize()
        return mt5.account_info()


mt5_connector = MT5Connector()
