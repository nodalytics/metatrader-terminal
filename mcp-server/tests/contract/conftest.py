"""Drive the tool handlers against the bridge's own FastAPI application.

The application is imported unmodified and only `MetaTrader5` is replaced, by
the same fake the bridge's own tests use - `MT5/api/tests/unit/fake_mt5.py`. So
what these tests exercise is the real routing, the real request models and the
real services, with the terminal standing in. That is the point: a tool that
sends `count` where the route declares `num_bars`, or a body where the route
reads the query string, is a well-formed request that FastAPI answers
successfully and wrongly. Only the real app can tell those apart; a mock of the
HTTP layer would be a mock of the thing under test.

`sys.modules["MetaTrader5"]` has to be set before `app` is imported, because
every module in `app` does `import MetaTrader5 as mt5` at module scope. The
same is true of two environment variables: `app.utils.config` builds its
settings object at import and `app.db.database` builds its engine from
`DATABASE_URL` at import, so setting either from a fixture is too late. That
ordering is why this is a conftest and not a fixture.

`API_KEY_SEED` is set to a throwaway value rather than left empty on purpose.
Empty disables the bridge's authentication entirely, and a suite that ran that
way would pass whether or not the client sent a credential - including if it
sent the seed instead of the derived key, or put it in the wrong header.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
API_ROOT = REPO / "MT5" / "api"
FAKE_DIR = API_ROOT / "tests" / "unit"
MCP_ROOT = REPO / "mcp-server"

#: The seed the bridge is configured with for these tests. The key a client
#: must send is its SHA-256, which is what `Settings.api_key` computes and what
#: `POST /api/v1/auth/login` hands back.
SEED = "mcp-server-contract-tests"
API_KEY = hashlib.sha256(SEED.encode("utf-8")).hexdigest()

_DB = Path(tempfile.mkdtemp(prefix="mt5-mcp-contract-")) / "contract.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["API_KEY_SEED"] = SEED
# The bridge's lifespan binds a socket for the EA tick feed. These tests never
# start the lifespan, but the setting is turned off so that a future change
# that does start it cannot fail on a port another process holds.
os.environ["EA_FEED_ENABLED"] = "false"

for path in (str(FAKE_DIR), str(API_ROOT), str(MCP_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import fake_mt5  # noqa: E402

sys.modules.setdefault("MetaTrader5", fake_mt5)

from mt5_mcp.bridge import Bridge  # noqa: E402
from mt5_mcp.tools import Tools  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """asyncio only. The handlers are plain coroutines with no trio-specific
    behaviour, and running the suite twice would double the cost for no
    second answer."""
    return "asyncio"


@pytest.fixture()
def terminal(monkeypatch, tmp_path):
    """A clean fake terminal, connected, per test.

    The connector is driven through its real `_do_initialize` against a login
    marker, not stubbed to `_initialized = True`, so the connect path stays
    under test. It is called synchronously rather than through `_start_init`,
    which spawns a daemon thread a test could race.

    `_do_initialize` calls `os._exit(1)` after `MAX_IPC_RETRIES` failures,
    which in a test process kills the runner with no report - so nothing here
    may make the fake refuse to initialise.
    """
    from app.services import connector

    fake_mt5.state.reset()
    marker = tmp_path / "login_complete"
    marker.write_text("")
    monkeypatch.setattr(connector, "LOGIN_MARKER", str(marker))
    monkeypatch.setattr(connector.mt5_connector, "_initialized", False)
    monkeypatch.setattr(connector.mt5_connector, "_initializing", False)
    monkeypatch.setattr(connector.mt5_connector, "_ipc_failures", 0)
    connector.mt5_connector._do_initialize()
    _clear_bridge_cache()
    yield fake_mt5.state


def _clear_bridge_cache() -> None:
    """Empty the bridge's symbol and tick cache between tests.

    It holds a symbol specification for 300 seconds and a tick for one, keyed
    by name and in a module-level singleton that outlives any fixture. Without
    this, a test that sets a symbol's `filling_mode` would be answered with
    whatever an earlier test's symbol of the same name had, and both would
    pass or fail depending on the order they ran in.
    """
    from app.utils.cache import cache_manager

    cache_manager.clear()


@pytest.fixture()
def app(terminal):
    """The bridge's FastAPI application, built per test.

    `create_app()` rather than a module-level instance because the exception
    handlers and routers are registered inside it, and a shared app would
    carry one test's database session factory into the next.
    """
    from app.main import create_app

    return create_app()


@pytest.fixture()
async def tools(app):
    """The tool handlers, wired to the application over an in-process transport.

    `httpx.ASGITransport` rather than a running server: it is the same client
    code and the same application, with the socket removed. The credential is
    sent the way a deployed server sends it, through `X-API-Key`, so a test
    that reaches a protected route proves the header name as well as the route.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://bridge", headers={"X-API-Key": API_KEY}
    ) as client:
        yield Tools(Bridge(client))


@pytest.fixture()
def calls(terminal):
    """Everything the fake terminal was asked, as `(name, args, kwargs)`.

    The assertions that matter are about *what reached the terminal*: a route
    whose parameter was dropped still answers 200, so the only way to tell the
    requested window from the bridge's 30-day fallback is to read the argument
    `history_deals_get` was actually called with.
    """
    return terminal.calls


def last_call(calls, name):
    """The most recent call to `name`, or a failure naming what was called instead."""
    for entry in reversed(calls):
        if entry[0] == name:
            return entry
    raise AssertionError(f"the terminal was never asked {name}; it saw {[c[0] for c in calls]}")
