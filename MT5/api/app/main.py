import sys
import logging
from contextlib import asynccontextmanager
try:
    from app.services.terminal_transport import mt5
except ImportError:
    print("CRITICAL ERROR: MetaTrader5 library is not installed. This API requires MetaTrader5 to function.")
    sys.exit(1)

from fastapi import FastAPI, Request, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.routers import trading, auth, account, positions, symbols, history, terminal, orders, stream
from app.dependencies.auth import verify_api_key
from app.db.database import init_db
from app.services.feed import feed
from app.utils.config import settings
from app.utils.exceptions import MT5BaseException
from app.utils.logger import logger_instance
from apscheduler.schedulers.background import BackgroundScheduler
from app.utils.trailing import trailing_stop_handler
from prometheus_fastapi_instrumentator import Instrumentator

logger = logger_instance.get_logger()
scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup event
    logger.info("Initializing Database...")
    init_db()
    
    # Secure API Key Generation
    if settings.env.API_KEY_SEED:
        logger.info(f"API Key successfully generated from seed. Use this for Authentication: {settings.api_key}")
    else:
        logger.warning("No API_KEY_SEED found! Authentication will be disabled.")

    # Start scheduled tasks
    logger.info("Starting Background Scheduler...")
    scheduler.add_job(trailing_stop_handler, "interval", seconds=20)
    scheduler.start()

    # The EA's tick feed. Started here rather than lazily on a first
    # subscriber because the EA connects out on its own schedule - it is
    # attached to a chart and retries every second until something answers -
    # so a listener that waited for a consumer would drop every tick until one
    # arrived, and that consumer would see a push feed beginning mid-session.
    if settings.env.EA_FEED_ENABLED:
        try:
            await feed.start(settings.env.EA_FEED_HOST, settings.env.EA_FEED_PORT)
        except OSError as exc:
            # A port already in use means a second API process, and the
            # running one owns the feed. Loud rather than fatal: the poll
            # still works, and refusing to serve at all over a feature that
            # degrades would be the worse trade.
            logger.error(
                f"EA feed listener could not bind "
                f"{settings.env.EA_FEED_HOST}:{settings.env.EA_FEED_PORT}: {exc}. "
                f"/stream will poll instead of pushing."
            )

    yield

    # Shutdown event
    logger.info("Shutting down scheduler...")
    scheduler.shutdown()
    await feed.stop()

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.env.API_NAME,
        description=settings.env.API_DESCRIPTION,
        version=settings.env.API_VERSION,
        debug=settings.env.API_DEBUG_MODE,
        lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(MT5BaseException)
    async def mt5_exception_handler(request: Request, exc: MT5BaseException):
        try:
            mt5_code, mt5_msg = mt5.last_error()
        except Exception:
            mt5_code, mt5_msg = None, None

        # `kind` and `retryable` first, and on every error body this service
        # produces. A broker refusing a trade and a bad API key are both 4xx,
        # so a client that wanted to tell them apart had to match on the text
        # of `error` - and one client reported a broken deployment as an
        # ordinary bad trade because of it. These two are what a client
        # branches on; `error` is for a person.
        body = {
            "kind": exc.kind,
            "retryable": exc.is_retryable(),
            "error": exc.message,
        }
        if exc.code is not None:
            body["code"] = exc.code
        if mt5_code:
            body["mt5_code"] = mt5_code
            body["mt5_msg"] = mt5_msg

        logger.error(
            f"MT5 error [{exc.__class__.__name__}] kind={exc.kind} "
            f"retryable={exc.is_retryable()}: {exc.message} (mt5={mt5_code})"
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """A malformed request, in the same envelope as everything else.

        **FastAPI's default 422 body has a different shape from this
        service's errors** - `{"detail": [...]}` against `{"error": ...}` - so
        a client had to parse two formats to find out what went wrong. It is
        also the status a client now gets for an over-long order `comment`, a
        case where knowing it is `validation` and not `refusal` is the
        difference between fixing the request and retrying it forever.

        `detail` is kept verbatim alongside, because it names the offending
        field and nothing else here does.
        """
        return JSONResponse(
            status_code=422,
            content={
                "kind": "validation",
                "retryable": False,
                "error": "the request was rejected before the terminal saw it",
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """`HTTPException` in the same envelope, so auth is machine-readable.

        `dependencies/auth.py` raises a bare 401 and routes raise 404s, which
        came back as `{"detail": ...}`. A 401 is the one error a client must
        never confuse with a trading refusal: retrying it cannot help, and
        treating it as "the broker said no" hides a misconfigured deploy
        behind a bad-looking strategy.
        """
        kind = {401: "auth", 403: "auth", 404: "not_found", 429: "rate_limit"}.get(
            exc.status_code, "internal" if exc.status_code >= 500 else "validation"
        )
        # **`detail` is kept, not replaced.** The new fields are additive on
        # purpose: three tests and any existing client read `detail` from an
        # `HTTPException`, and breaking them to tidy a key would be paying for
        # the envelope with an outage. `error` carries the same text under the
        # name the rest of this service uses.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "kind": kind,
                "retryable": exc.status_code in (429, 503),
                "error": str(exc.detail),
                "detail": exc.detail,
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        try:
            mt5_code, mt5_msg = mt5.last_error()
        except Exception:
            mt5_code, mt5_msg = None, None

        body = {"kind": "internal", "retryable": False, "error": str(exc)}
        if mt5_code:
            body["mt5_code"] = mt5_code
            body["mt5_msg"] = mt5_msg

        logger.exception(f"Unhandled error on {request.method} {request.url.path}")
        return JSONResponse(status_code=500, content=body)

    # Health Check (Internal/System)
    @app.get("/health", tags=["System"])
    def health_check():
        """Whether this service is up **and whether a terminal is usable**.

        The second half was added 2026-09-24 after two outages that this route
        actively concealed.

        It used to return `{"status": "ok", "version": ...}` - true of the web
        service and silent about the only thing a caller cares about. So a
        consumer polling it saw `ok` while:

        * **no terminal was attached at all** - the desk retried once a minute
          for over a day against a bridge answering 200 here;
        * **AutoTrading was off** - the terminal was attached, this route said
          `ok`, and every order came back 10027 `AutoTrading disabled by
          client`.

        Both are now fields. `status` stays `ok` whenever the service can
        answer, because that is what it means and a load balancer depends on
        it; a caller that needs a *tradable* terminal reads `connected` and
        `trade_allowed`.

        **It never raises.** A health route that 500s when the terminal is
        missing tells you less than one that reports the terminal is missing,
        and it is the missing case this exists for. `build` is carried because
        the terminal upgrades itself from the broker and a bad build has broken
        IPC before - without it, "which build was that on?" is unanswerable
        after the fact.
        """
        body = {
            "status": "ok",
            "version": settings.env.API_VERSION,
            "connected": False,
            "trade_allowed": False,
            "build": None,
        }
        try:
            info = mt5.terminal_info()
        except Exception:
            return body
        if info is None:
            return body
        body["connected"] = bool(getattr(info, "connected", False))
        body["trade_allowed"] = bool(getattr(info, "trade_allowed", False))
        body["build"] = getattr(info, "build", None)
        return body

    @app.get("/feed", tags=["System"])
    def feed_status():
        """Whether an EA is pushing ticks, and what it has sent.

        **The one thing about the push feed that cannot be checked from
        outside the container.** The EA is MQL5 compiled in MetaEditor and
        attached to a chart by hand, and three separate things can silently
        not be true: it may not be compiled, the chart may not be open, or
        `127.0.0.1` may not be in Tools -> Options -> Expert Advisors, which
        MT5 checks for `SocketConnect` and not only for `WebRequest`.

        Every one of those looks identical from a consumer's side - a symbol
        that is polled instead of pushed - and `/api/v1/stream` deliberately
        does not fall back silently, so a client asking `require=ea` is
        refused. This route is how an operator finds out *which* of the three
        it is, in one request, instead of reading a terminal log through VNC.

        `symbols` is the answer to "is my EA working": a name appears there
        only once a frame has actually arrived for it, and leaves after two
        minutes of silence. `gaps` is the answer to the question after that -
        frames the EA stamped and that did not arrive, which is a tick the
        consumer has to refill from `copy_ticks_range`.

        Unprotected, like `/health` and for the same reason: a probe should
        not need a credential to say whether the thing is working, and this
        carries no account information.
        """
        return {
            "enabled": settings.env.EA_FEED_ENABLED,
            "listening": f"{settings.env.EA_FEED_HOST}:{settings.env.EA_FEED_PORT}",
            **feed.tally,
        }

    # Auth routes (Unprotected)
    app.include_router(auth.router, prefix="/api/v1")

    # Protected routes
    app.include_router(
        trading.router, 
        prefix="/api/v1", 
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        account.router, 
        prefix="/api/v1", 
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        positions.router, 
        prefix="/api/v1", 
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        symbols.router, 
        prefix="/api/v1", 
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        history.router,
        prefix="/api/v1",
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        orders.router,
        prefix="/api/v1",
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        terminal.router, 
        prefix="/api/v1", 
        dependencies=[Depends(verify_api_key)]
    )
    # The stream authenticates itself. `verify_api_key` is an HTTP dependency
    # and cannot run on a WebSocket handshake — a browser cannot set the header
    # it reads either, so the socket takes the key from the query string as
    # well. See `stream._authorised`.
    app.include_router(stream.router, prefix="/api/v1")

    # **Two registrations, not one `api_route` with both methods.** FastAPI
    # derives an operation ID from the route's name and the *first* of its
    # methods, so `methods=["GET", "HEAD"]` emitted two operations claiming
    # `read_root__get` and the generated spec warned
    # `Duplicate Operation ID` - which collides in any client generated from
    # it. HEAD is here for probes that do not want a body and is kept out of
    # the schema, so the document describes one root operation.
    @app.get("/", tags=["System"], operation_id="read_root")
    def read_root():
        return {"message": "Welcome to MetaTrader 5 API", "docs": "/docs"}

    @app.head("/", tags=["System"], include_in_schema=False)
    def head_root():
        return {"message": "Welcome to MetaTrader 5 API", "docs": "/docs"}

    # Instrument FastAPI
    Instrumentator().instrument(app).expose(app)

    return app

app = create_app()