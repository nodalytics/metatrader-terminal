"""The MCP server: every tool in `tools.TOOL_NAMES`, over stdio.

Registration is driven from the registry rather than from decorators on the
handlers. A decorator registers whatever it is attached to, which makes "the
tools this server offers" a property of where someone remembered to put one; a
list makes it a thing a test can compare against, and
`tests/registry/test_registration.py` does exactly that. A handler written and
never registered is then a failing test instead of a capability an agent never
discovers.

Every tool is registered unconditionally, reads and writes alike. This server
is for an agent that trades without a human in the loop, and a capability that
has to be switched on is one such an agent finds missing with no way to ask
for it. What the annotations do carry is whether a call only reads, so a client
that wants to show a confirmation can tell the two apart - a statement about
the tool, not a gate on it.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .bridge import Bridge
from .tools import TOOL_NAMES, WRITE_TOOLS, Tools

#: What the client shows about this server before any tool is called.
#:
#: It says the account is live and writable, because the alternative - letting
#: an agent discover that from the tool list - invites it to plan a read-only
#: workflow around capabilities it actually has, or to assume a confirmation
#: step exists that does not.
INSTRUCTIONS = """\
Trades a live MetaTrader 5 account through its HTTP bridge.

Reads terminal and account state, instrument specifications, live quotes, OHLC
bars, open positions, resting orders and deal history; places market and
pending orders, moves stops and targets, closes positions in full or in part,
and cancels resting orders. There is no confirmation step and no dry run: a
call to a write tool reaches the broker.

Three things decide whether an order works, and each is reported rather than
assumed. `account_status.autotrading_enabled` is the terminal's AutoTrading
button and is the flag an order is checked against - the account's own
`broker_allows_trading` is a different flag and reads `true` while every order
is refused with 10027. The fill policy is a property of the symbol, derived
from its bitmask by the order tools. And every price-like field states its
unit: the spread is in points, money is in the account's currency, and the
bid/ask carried by an instrument's specification is frozen and is never
reported - the only quotes here are live ticks.

Every write answers with MT5's raw retcode, a plain meaning and a remedy.\
"""

#: Which tools only read, as hints on the registration.
#:
#: `destructiveHint` is reserved for the three that remove something that
#: existed - a position, the whole book, a resting order. Placing and
#: modifying are additive or reversible, and marking them destructive too
#: would make the flag say nothing.
_DESTRUCTIVE = frozenset({"close_position", "close_all_positions", "cancel_pending_order"})


def annotations_for(name: str) -> ToolAnnotations:
    """The hints for one tool: read-only, destructive, and open-world.

    `openWorldHint` is true on all of them because every call reaches a broker
    over a network: the answer depends on a market and on a terminal that this
    process does not own, so no call here is a pure function of its arguments.
    """
    write = name in WRITE_TOOLS
    return ToolAnnotations(
        readOnlyHint=not write,
        destructiveHint=name in _DESTRUCTIVE,
        idempotentHint=False,
        openWorldHint=True,
    )


def build_server(tools: Tools, name: str = "metatrader5") -> FastMCP:
    """A server with every registry tool registered against `tools`.

    Takes the handlers rather than building them, so a test can register a
    `Tools` over a bridge that is never called and still assert what the
    server offers. `getattr` raises on a name in the registry with no handler,
    which is the failure worth having loudly at startup.
    """
    mcp = FastMCP(name=name, instructions=INSTRUCTIONS)
    for tool_name in TOOL_NAMES:
        handler = getattr(tools, tool_name)
        mcp.add_tool(handler, name=tool_name, annotations=annotations_for(tool_name))
    return mcp


def main() -> None:
    """Serve on stdio, against the bridge named by `MT5_API_URL`.

    The bridge is built before the server, so a missing `MT5_API_URL` stops
    the process with a message naming the variable instead of starting a
    server whose every tool fails with a connection error that mentions no
    configuration at all.
    """
    bridge = Bridge.from_env()
    build_server(Tools(bridge)).run(transport="stdio")


if __name__ == "__main__":
    main()
