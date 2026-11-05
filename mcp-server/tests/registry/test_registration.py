"""What the MCP server actually offers a client, and how it describes it.

These run in a different environment from `tests/contract`, and not by
preference: the bridge's application pins `pydantic<2.0.0` while the MCP SDK
requires pydantic 2, so the two cannot be installed side by side. The split is
along the same seam as the code - `mt5_mcp.tools` imports no SDK and is tested
against the real bridge, `mt5_mcp.server` imports the SDK and is tested here.

What is left to pin on this side is everything an agent sees *before* it calls
anything: which tools exist, whether each one reads or writes, and whether the
descriptions carry the units without which a number is a guess.
"""

from __future__ import annotations

import httpx
import pytest

from mt5_mcp.bridge import Bridge, endpoint_from_env
from mt5_mcp.retcodes import explain
from mt5_mcp.server import INSTRUCTIONS, build_server
from mt5_mcp.tools import TOOL_NAMES, WRITE_TOOLS, Tools

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def server():
    """A server over a bridge that is never called.

    Registration is a property of the server and the handlers, not of the
    terminal, so the client underneath goes nowhere. A test that needed a
    reachable bridge to find out which tools exist would be a test of the
    bridge.
    """
    return build_server(Tools(Bridge(httpx.AsyncClient(base_url="http://unused"))))


async def test_every_registered_tool_is_in_the_registry_and_the_reverse(server):
    """The listing and `TOOL_NAMES` must be the same set.

    Registration is driven from the list, so the two can only drift by a
    handler being renamed without the list, or a name being added to the list
    with no handler. The first makes a tool vanish from an agent's view while
    the code that implements it still exists and still passes its own tests;
    the second is an `AttributeError` at startup, which is better but worth
    catching before a client sees it.
    """
    listed = {tool.name for tool in await server.list_tools()}

    assert listed == set(TOOL_NAMES)
    assert len(listed) == len(TOOL_NAMES), "a name is registered twice"


async def test_the_write_tools_are_all_present(server):
    """An agent that trades without a human must find the whole order surface.

    Every order type the bridge supports has to be reachable: market both
    ways, the four limit and stop types and the two stop-limits through the
    pending tool, modification of a position and of a resting order, a full
    close, a partial close, a close-all and a cancel. A missing one is not a
    degraded server - it is an agent that can open a position and cannot exit
    it.
    """
    listed = {tool.name for tool in await server.list_tools()}

    assert WRITE_TOOLS <= listed
    for required in (
        "place_market_order",
        "place_pending_order",
        "modify_position",
        "modify_pending_order",
        "close_position",
        "close_all_positions",
        "cancel_pending_order",
    ):
        assert required in listed


async def test_reads_and_writes_are_marked_apart(server):
    """`readOnlyHint` must distinguish the two, since nothing else does.

    A client deciding whether to confirm a call has only the annotation to go
    on. Marked read-only, a close would be executed without a prompt in any
    client that offers one; marked writable, a quote would prompt, and a
    client that prompts for quotes gets its prompts turned off.
    """
    for tool in await server.list_tools():
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name} carries no annotations"
        expected = tool.name not in WRITE_TOOLS
        assert annotations.readOnlyHint is expected, f"{tool.name} is marked the wrong way round"


async def test_only_the_tools_that_remove_something_are_marked_destructive(server):
    """`destructiveHint` has to mean something narrower than "it writes".

    Placing an order and moving a stop are additive or reversible; closing a
    position, flattening the book and cancelling a resting order are not. If
    every write carried the flag it would carry no information, and a client
    using it to decide what needs a second look would either confirm
    everything or nothing.
    """
    destructive = {
        tool.name
        for tool in await server.list_tools()
        if tool.annotations and tool.annotations.destructiveHint
    }

    assert destructive == {"close_position", "close_all_positions", "cancel_pending_order"}


async def test_every_tool_has_a_description_and_an_argument_schema(server):
    """A tool with no description is a tool an agent will not call correctly.

    The descriptions come from the handlers' docstrings, so a handler whose
    docstring is deleted - or replaced by a one-line summary during a tidy-up
    - silently removes the units and the failure modes an agent depends on.
    The floor is deliberately low and the point is that it is a floor.
    """
    for tool in await server.list_tools():
        assert tool.description and len(tool.description) > 200, f"{tool.name} is barely described"
        assert tool.inputSchema.get("type") == "object", f"{tool.name} has no argument schema"


async def test_the_descriptions_carry_the_units_that_have_been_misread(server):
    """Units an agent cannot see are units it will assume.

    Each entry below is a unit this stack has got wrong at least once. The
    spread in points read as price produced a 476% round trip on an FX pair
    and reordered every instrument by cost; money read in the instrument's
    currency rather than the account's mis-sizes every cross; a volume in lots
    read as units is refused with 10014 at best and is a hundred-thousand-fold
    error at worst.
    """
    described = {tool.name: tool.description for tool in await server.list_tools()}

    assert "points, not price" in described["instrument_spec"]
    assert "lots" in described["instrument_spec"]
    assert "account" in described["instrument_spec"] and "currency" in described["instrument_spec"]
    assert "currency" in described["account_status"]
    assert "lots" in described["place_market_order"]
    assert "points" in described["place_market_order"]
    assert "not distances" in described["place_market_order"]
    assert "points" in described["bars"]


async def test_the_quote_tools_say_which_price_is_live(server):
    """The frozen bid/ask has to be named as frozen where an agent will look.

    An agent reaching for a price reads the instrument tool first, because
    that is where a price belongs. The fields are not there, and a tool that
    merely omits them without saying why invites the agent to go looking for
    them on another route - which is the one that is frozen.
    """
    described = {tool.name: tool.description for tool in await server.list_tools()}

    assert "live" in described["instrument_spec"]
    assert "frozen" in described["instrument_spec"] or "snapshot" in described["instrument_spec"]
    assert "live" in described["quote"]


async def test_the_server_instructions_name_the_flag_that_decides_an_order(server):
    """The two `trade_allowed` flags have to be told apart before the first call.

    An agent that reads the account's flag concludes it can trade, places an
    order, is refused with 10027, and has no reason to look at a second flag
    with the same name. The instructions are where it finds out first, and
    they also have to say that writes reach the broker - a server that looked
    read-only until a tool was called would be planned around wrongly.
    """
    assert "autotrading_enabled" in INSTRUCTIONS
    assert "10027" in INSTRUCTIONS
    assert "no dry run" in INSTRUCTIONS


async def test_calling_a_tool_through_the_server_reaches_the_handler(server):
    """Registration has to produce a call, not just a listing.

    A tool can be listed and still be unreachable - bound to the wrong object,
    or registered under a name the dispatcher does not resolve. This drives
    `account_status` through the server's own dispatch to a bridge that is not
    listening, so the failure that arrives proves the call was attempted. A
    tool that was listed but not wired would fail differently: unknown tool,
    not unreachable bridge.
    """
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as failed:
        await server.call_tool("account_status", {})

    assert "could not reach the bridge" in str(failed.value)


def test_a_missing_endpoint_stops_the_server_and_names_the_variable():
    """`MT5_API_URL` has no default, because a wrong default is invisible.

    Defaulted to localhost, the server would start, list every tool, and fail
    every call with a connection error that mentions no configuration at all -
    and an agent reading that concludes the terminal is down. Named here, the
    operator is told which variable to set.
    """
    with pytest.raises(RuntimeError) as missing:
        endpoint_from_env({})

    assert "MT5_API_URL" in str(missing.value)


def test_an_absent_api_key_is_allowed_because_the_bridge_may_not_require_one():
    """The bridge disables authentication entirely when its seed is unset.

    Demanding a key would make that configuration unusable. The key is also
    the *derived* one - `sha256(API_KEY_SEED)` - and not the seed; sending the
    seed produces 401 on every protected route, which reads like a wrong key
    rather than a key that was never hashed, so the distinction is documented
    where the variable is read.
    """
    url, key = endpoint_from_env({"MT5_API_URL": "http://bridge:8000/"})

    assert url == "http://bridge:8000"
    assert key == ""
    assert "sha256" in endpoint_from_env.__doc__.lower() or "SHA-256" in endpoint_from_env.__doc__


@pytest.mark.parametrize(
    ("code", "ok", "word"),
    [
        (10009, True, "done"),
        (10008, True, "resting"),
        (10027, False, "AutoTrading"),
        (10030, False, "filling"),
        (10014, False, "volume"),
        (10019, False, "money"),
        (10016, False, "stops"),
    ],
)
def test_the_retcodes_that_actually_happen_are_legible(code, ok, word):
    """An agent reading `10027` learns nothing from the number alone.

    These seven are the codes this stack has seen in practice. Each has a
    different remedy - turn on a button, change the filling mode, round the
    volume, reduce the size, widen the stop - and an agent handed only the
    integer cannot tell them apart, so it will retry the same order and be
    refused the same way.
    """
    reading = explain(code)

    assert reading["retcode"] == code
    assert reading["ok"] is ok
    assert word.lower() in (reading["retcode_meaning"] + " " + reading["remedy"]).lower()


def test_an_unrecognised_retcode_is_reported_as_unrecognised():
    """A code MetaQuotes adds later must not be silently called a failure.

    Mapping the unknown to "failed" would turn a new success code into an
    agent that re-sends an order it has already placed. Reported as
    unrecognised, with the number intact, the agent can at least read the
    position list before acting again.
    """
    reading = explain(19_999)

    assert reading["ok"] is False
    assert "19999" in reading["retcode_meaning"]
