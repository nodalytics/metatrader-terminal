"""An MCP server over the MetaTrader 5 HTTP bridge in `MT5/api`.

`mt5_mcp.bridge` holds the route contracts, `mt5_mcp.tools` the handlers an
agent calls, and `mt5_mcp.server` the registration. Only `server` imports the
MCP SDK, which is what lets the other two be tested against the bridge's own
FastAPI application: that application pins `pydantic<2.0.0` and the SDK
requires pydantic 2, so the two cannot be installed together.
"""

__all__ = ["bridge", "retcodes", "server", "tools"]
