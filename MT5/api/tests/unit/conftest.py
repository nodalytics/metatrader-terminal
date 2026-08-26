"""Run the real application against a fake terminal.

The application is imported unmodified. Only `MetaTrader5` is replaced, and it
is replaced in `sys.modules` **before** `app` is imported, because every module
in `app` does `import MetaTrader5 as mt5` at module scope — by the time a test
function runs it is far too late.

That ordering is the whole trick, and it is why this lives in a conftest rather
than in a fixture: conftest import happens before test collection, which is
before anything imports the app.

These tests need no container, no broker and no network, so they can run in CI
— which until now had nothing to run but "does the image start".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
API_ROOT = HERE.parent.parent

# The fake has to be importable by its own name and installed under the name
# the application will ask for.
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_ROOT))

import fake_mt5  # noqa: E402

sys.modules.setdefault("MetaTrader5", fake_mt5)


@pytest.fixture(autouse=True)
def terminal(tmp_path, monkeypatch):
    """A clean fake terminal per test, connected, with a throwaway database.

    The connector is driven through its **real** `_do_initialize`, against a
    login marker in `tmp_path` — not stubbed to `_initialized = True`. That
    keeps the connect path itself under test, which matters because it is the
    part that has been changed most often.

    It is called synchronously rather than through `_start_init`, which spawns
    a daemon thread; a test that raced it would be flaky for a reason having
    nothing to do with what it was testing.

    One warning about this code path: `_do_initialize` calls `os._exit(1)` when
    the terminal refuses to connect `MAX_IPC_RETRIES` times, which in a test
    process kills the runner with no report. Any test of the failure path must
    stop short of that.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("API_KEY_SEED", "")
    fake_mt5.state.reset()

    from app.services import connector

    marker = tmp_path / "login_complete"
    marker.write_text("")
    monkeypatch.setattr(connector, "LOGIN_MARKER", str(marker))
    monkeypatch.setattr(connector.mt5_connector, "_initialized", False)
    monkeypatch.setattr(connector.mt5_connector, "_initializing", False)
    monkeypatch.setattr(connector.mt5_connector, "_ipc_failures", 0)
    connector.mt5_connector._do_initialize()

    yield fake_mt5.state


@pytest.fixture()
def app_client(terminal):
    """A TestClient over the real FastAPI app.

    Built per test rather than per session because the app reads settings at
    import and the database URL is per-test. The import cost is small next to
    the confusion of tests sharing a terminal's state.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
