"""Collect whichever suite this interpreter is equipped for.

The two suites cannot share an environment: `tests/contract` imports the
bridge's FastAPI application, which pins `pydantic<2.0.0`, and
`tests/registry` imports the MCP SDK, which requires pydantic 2. Without this,
a bare `pytest` in either environment collects both and reports a wall of
import errors from the suite that was never meant to run there, which hides
whatever is actually wrong with the one that was.

CI names each suite by path and does not rely on this. If it ever ran in the
wrong environment the named directory would be skipped, pytest would collect
nothing and exit non-zero, so the mistake fails rather than passing quietly.
"""

from __future__ import annotations

import importlib.util

collect_ignore: list[str] = []

if importlib.util.find_spec("fastapi") is None:
    collect_ignore.append("tests/contract")

if importlib.util.find_spec("mcp") is None:
    collect_ignore.append("tests/registry")


def pytest_report_header(config) -> str:
    """Say which suite was left out, so an empty run is never a mystery."""
    if not collect_ignore:
        return "mt5_mcp: both suites collected"
    return (
        f"mt5_mcp: not collecting {', '.join(collect_ignore)} - "
        "this environment lacks its dependencies (see README.md)"
    )
