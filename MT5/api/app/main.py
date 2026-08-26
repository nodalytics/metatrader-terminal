import sys
import logging
from contextlib import asynccontextmanager
try:
    import MetaTrader5 as mt5
except ImportError:
    print("CRITICAL ERROR: MetaTrader5 library is not installed. This API requires MetaTrader5 to function.")
    sys.exit(1)

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.routers import trading, auth, account, positions, symbols, history, terminal, orders, stream
from app.dependencies.auth import verify_api_key
from app.db.database import init_db
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
    
    yield
    
    # Shutdown event
    logger.info("Shutting down scheduler...")
    scheduler.shutdown()

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

        body = {"error": exc.message}
        if exc.code is not None:
            body["code"] = exc.code
        if mt5_code:
            body["mt5_code"] = mt5_code
            body["mt5_msg"] = mt5_msg

        logger.error(f"MT5 error [{exc.__class__.__name__}]: {exc.message} (mt5={mt5_code})")
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        try:
            mt5_code, mt5_msg = mt5.last_error()
        except Exception:
            mt5_code, mt5_msg = None, None

        body = {"error": str(exc)}
        if mt5_code:
            body["mt5_code"] = mt5_code
            body["mt5_msg"] = mt5_msg

        logger.exception(f"Unhandled error on {request.method} {request.url.path}")
        return JSONResponse(status_code=500, content=body)

    # Health Check (Internal/System)
    @app.get("/health", tags=["System"])
    def health_check():
        return {"status": "ok", "version": settings.env.API_VERSION}

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

    @app.api_route("/", methods=["GET", "HEAD"], tags=["System"])
    def read_root():
        return {"message": "Welcome to MetaTrader 5 API", "docs": "/docs"}

    # Instrument FastAPI
    Instrumentator().instrument(app).expose(app)

    return app

app = create_app()