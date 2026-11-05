# MetaTrader 5 MCP server

An MCP server over the FastAPI bridge in [`MT5/api`](../MT5/api). It gives an
agent the whole account: terminal and account state, instrument
specifications, live quotes, OHLC bars, open positions, resting orders and
deal history, and every order operation the bridge supports — market and
pending entries, stop and target moves, full and partial closes, a close-all,
and cancels.

**Every tool is registered, reads and writes alike.** There is no
confirmation step and no dry run: a call to a write tool reaches the broker.
This is built for an agent trading without a human in the loop, and a
capability behind a switch is one such an agent finds missing with no way to
ask for it. What the registration does carry is `readOnlyHint` and
`destructiveHint` per tool, so a client that wants to prompt can tell a quote
from a close.

## The tools

The tools are shaped around what has to be decided, not around the bridge's
routes — `account_status` is two routes, `instrument_spec` is two, and
`trade_history` is one or two depending on what was asked for.

### Reading

| tool | answers | notes |
| :--- | :--- | :--- |
| `account_status` | can an order be placed right now, and what is there to size with | `autotrading_enabled` is the terminal's flag — the one an order is checked against. Money is in the account's currency, which is named beside it |
| `search_symbols` | what this broker offers, narrowed by substring | names only; they are case-sensitive everywhere else |
| `instrument_spec` | what one instrument costs and how it must be sized | spread in points **and** in price, tick value in the account's currency, the permitted fill policies, and a **live** quote |
| `quote` | where the market is, for up to 40 instruments at once | one unknown name does not cost the others their quotes |
| `bars` | OHLC, as the most recent *n* or over a stated window | the last bar of a recent-bars answer is still forming |
| `open_positions` | what the account is holding | `magic` filters to one strategy's trades; `magic=0` is a real value |
| `pending_orders` | what is resting and has not triggered | |
| `trade_history` | what happened — fills, and optionally the orders behind them | the window is always sent explicitly; there is no unbounded mode |

### Writing

| tool | does | notes |
| :--- | :--- | :--- |
| `place_market_order` | buy or sell at the market | fill policy derived from the symbol; `stop_loss` defaults to `0.0`, which is how MT5 spells "none" |
| `place_pending_order` | `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`, `SELL_STOP`, `BUY_STOP_LIMIT`, `SELL_STOP_LIMIT` | separate from the market tool on purpose — see below |
| `modify_position` | move an open position's stop and target, by ticket | prices, not distances |
| `modify_pending_order` | move a resting order's price, stop, target or expiry | `price` is required by the bridge's model |
| `close_position` | close in full, or **partially** with a smaller `volume` | |
| `close_all_positions` | flatten everything, one side, or one `magic` | the count is what closed, not what was attempted |
| `cancel_pending_order` | remove a resting order | |

Every write answers with MT5's raw `retcode`, a plain `retcode_meaning`, a
`remedy`, and an `ok` computed from the code. A refusal is a result, not an
exception: an agent reading `10027` needs to learn that the AutoTrading button
is off, not that it made a bad request.

### Why market and pending are two tools

The bridge's market route validates `order_type` against `^(BUY|SELL)$` and
its handler takes no price at all, so a limit sent there is accepted and
**filled at the market** — the opposite of what a resting entry is for, with
no error anywhere. One tool with an optional price would make that mistake
expressible.

### Two things the tools do on the caller's behalf

**Fill policy is resolved from the symbol.** `filling_mode` is a bitmask on the
symbol — 1 FOK, 2 IOC, 4 RETURN — and sending a policy the symbol does not
list is refused with `10030` on *every* order for that symbol, not on some of
them. The order tools read the mask, send a policy the symbol permits, and
report both `filling_used` and `filling_allowed`, so a preference that could
never have worked is visible rather than silently downgraded.

**The frozen bid/ask is never reported.** MT5's symbol specification carries a
`bid`/`ask` that the terminal refreshes on its own terms: eight reads three
seconds apart returned the same pair *and the same timestamp* while the tick
moved on every one. `instrument_spec` drops those two fields and quotes the
tick instead. Relabelling them would have left a stale price an agent could
still size off.

## Environment

| variable | meaning | default |
| :--- | :--- | :--- |
| `MT5_API_URL` | base URL of the bridge, e.g. `http://localhost:8000` | **none — required** |
| `MT5_API_KEY` | the bridge's API key, sent as `X-API-Key` | empty |

`MT5_API_URL` has no default on purpose. A default of localhost would be a
hardcoded endpoint that is wrong everywhere but one machine, and wrong in the
way that is hardest to see: the server would start, list every tool, and fail
every call with a connection error that names nothing about configuration.

`MT5_API_KEY` is the **derived** key — `sha256(API_KEY_SEED)`, which is what
`POST /api/v1/auth/login` hands back — and not the seed. Sending the seed
produces `401 Invalid or missing API Key` on every protected route, which
reads like a wrong key rather than a key that was never hashed. It may be
empty, because the bridge disables authentication entirely when its own
`API_KEY_SEED` is unset.

## Running it

```bash
cd mcp-server
pip install -e .

MT5_API_URL=http://localhost:8000 MT5_API_KEY=<the derived key> python -m mt5_mcp
```

It speaks MCP over stdio, so it is launched by the client rather than run as a
service.

### Client configuration

```json
{
  "mcpServers": {
    "metatrader5": {
      "command": "python",
      "args": ["-m", "mt5_mcp"],
      "cwd": "/path/to/metatrader-terminal/mcp-server",
      "env": {
        "MT5_API_URL": "http://localhost:8000",
        "MT5_API_KEY": "the-sha256-of-your-API_KEY_SEED"
      }
    }
  }
}
```

The bridge is normally reached over loopback or an SSH tunnel; the repository
[README](../README.md) covers both. Pointing this at a bridge across an open
network publishes an endpoint that can trade.

## Tests

Two suites, in **two environments**, and not by preference: the bridge pins
`pydantic<2.0.0` and the MCP SDK requires pydantic 2, so they cannot be
installed in the same interpreter. The code is split along the same seam —
`mt5_mcp.bridge` and `mt5_mcp.tools` import no SDK, and only `mt5_mcp.server`
does.

```bash
# the route contracts, against the bridge's real FastAPI application
python -m venv .venv-contract && .venv-contract/bin/pip install -r requirements-contract.txt
.venv-contract/bin/python -m pytest tests/contract

# the registration, the annotations and the descriptions
python -m venv .venv-registry && .venv-registry/bin/pip install -r requirements-registry.txt
.venv-registry/bin/python -m pytest tests/registry
```

Neither needs a terminal, a container, a broker or a network.

`tests/contract` runs the bridge's **real** application with only
`MetaTrader5` replaced, by the same fake the bridge's own tests use
(`MT5/api/tests/unit/fake_mt5.py`). That matters because the mistakes worth
catching here are not exceptions — they are well-formed requests that FastAPI
answers successfully and wrongly. It pins, among others:

- `POST /positions/close` reads **query parameters**; a JSON body is
  `422 field required` for `query.ticket`, and a partial close whose `volume`
  lands in a body becomes a full exit with a 200.
- `/history/*` spells the window `from_date`/`to_date`. The wrong spelling is
  dropped silently and substituted with 30 days — measured at 4,013 deals
  where the correct spelling returned 0.
- `/symbols/rates/pos` counts bars with `num_bars`, not `count`, and defaults
  to 100 when the parameter is dropped.
- `MarketOrderRequest.sl` has no default, so a market order without a stop
  never reaches the terminal at all.
- Each of the six resting order types arrives as its own MT5 constant, and at
  `TRADE_ACTION_PENDING` rather than as a market fill.
- A refusal arrives as a retcode an agent can branch on, not as prose — and
  the bridge's own `success: true`, which is true of every reply it manages to
  build, is not carried through.

`tests/registry` pins what an agent sees before it calls anything: that the
listing and the registry are the same set, that reads and writes are marked
apart, that only the three tools which remove something are marked
destructive, and that the descriptions still carry the units — the spread in
points, money in the account's currency, volume in lots — each of which has
been read as the wrong one at least once.

## Known limits of the bridge

- **Stop-limit orders cannot carry their limit price.** The bridge accepts
  `BUY_STOP_LIMIT` and `SELL_STOP_LIMIT` and sends only `price`, never MT5's
  separate `stoplimit` field, so the resting limit price is not expressible
  through the route. The four plain limit and stop types are unaffected.
- **Modifying a resting order requires its price.** `ModifyPendingOrderRequest`
  declares `price` without a default, so moving only a resting order's stop is
  not expressible; pass the order's current price to leave it where it is.
- **`close_all_positions` reports what closed.** The bridge logs a refusal on
  one position and continues to the next, so a count lower than the number of
  open positions means some were refused rather than that fewer were open.
