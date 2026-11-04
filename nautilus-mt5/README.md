# nautilus-mt5

A [NautilusTrader](https://nautilustrader.io) adapter for MetaTrader 5, talking
to this repository's HTTP bridge rather than to the terminal directly.

```bash
pip install -e nautilus-mt5
```

## Why over the bridge

`MetaTrader5` is a Windows package that makes in-process calls into a running
terminal. An adapter built on it has to run on the same machine as MT5, under
Windows or Wine. This one talks HTTP to the bridge, so the terminal stays where
it has to be and the trading node runs anywhere - which matters when the
terminal is on one box and the node should sit near the broker.

Nautilus has no official MT5 integration. The community adapters are all
unofficial and most require a local terminal.

## Use

```python
from nautilus_trader.live.node import TradingNode
from nautilus_trader.live.config import TradingNodeConfig

from nautilus_mt5 import (
    MT5DataClientConfig,
    MT5ExecClientConfig,
    MT5LiveDataClientFactory,
    MT5LiveExecClientFactory,
)

config = TradingNodeConfig(
    trader_id="TRADER-001",
    data_clients={"MT5": MT5DataClientConfig(
        base_url="http://bridge:8000",
        api_key="...",
    )},
    exec_clients={"MT5": MT5ExecClientConfig(
        base_url="http://bridge:8000",
        api_key="...",
        magic=777_701,
    )},
)

node = TradingNode(config=config)
node.add_data_client_factory("MT5", MT5LiveDataClientFactory)
node.add_exec_client_factory("MT5", MT5LiveExecClientFactory)
node.build()
node.run()
```

## Two transports, and nothing above the package knows which

`base_url` reaches the terminal over the bridge's HTTP API. The other way is
`rpyc`: a Windows Python in the Wine prefix serves the `MetaTrader5` module
and this gets a **proxy of the module** back - lower latency, the whole MT5
surface rather than the subset wrapped in routes, and no JSON round trip.

```python
MT5ExecClientConfig(transport="rpyc", rpyc_host="127.0.0.1")
```

Both answer the same `Transport` protocol, so the choice is one config field
and nothing above this package can tell them apart. **An rpyc server is a
remote-code-execution channel by construction** - it runs whatever its client
asks, which is the point of a module proxy - so a host that is not loopback is
refused unless `TRADING_RPYC_TUNNELLED=1` asserts the path is a private
network or an SSH tunnel. Use HTTP for anything crossing a network you do not
control.

## Letting the node start the terminal

The same shape as the Interactive Brokers adapter's `DockerizedIBGateway`: a
config field, and the factory starts the container and waits for it.

```python
MT5ExecClientConfig(
    dockerized_terminal=DockerizedMT5TerminalConfig(
        login=12345678, password="...", server="Deriv-Demo",
        setup_url="https://.../mt5setup.exe",
    ),
)
```

Readiness is `/health`'s `connected`, not the container's status, because the
image's own README warns that `status` reflects only the web service: a
container that is `running` is your side of the wire. `trade_allowed` is
reported without being required - a connected terminal with trading closed is
a weekend, not a failure.

`setup_url` is worth setting. It pins which terminal installer the image
fetches, and an unpinned one has produced a terminal whose IPC the Python
binding could not speak: build 6140 works, 6204 does not.

## Instruments

A forex pair becomes a `CurrencyPair` and everything else - index CFDs,
metals, crypto, the synthetics - becomes a `Cfd`, decided by MT5's own
`trade_calc_mode` rather than by the symbol's name. A name is a broker's
marketing, and keying on it means every new broker needs a new rule found in
production.

This matters more than a taxonomy. `US500` reports `currency_base="USD"` and
`currency_profit="USD"`, so modelling it as a `CurrencyPair` made a pair of
USD against itself - an index classified and margined as FX, with nothing
raising. A `Cfd` accepts the absence of a base currency; a `CurrencyPair`
cannot.

Nautilus fixes a CFD's multiplier at 1, so `trade_contract_size` - 100 ounces
for gold, 1 for an index - stays in `instrument.info` where sizing reads it.

## What this feed carries, and what it does not

Measured against a live terminal, not inferred from the API surface:

| data | available |
| --- | --- |
| quotes | yes, polled |
| bars | yes |
| historical bars and ticks | yes, and **complete** |
| order book | **no** - `market_book_add` fails outright |
| trade ticks | **no** - MT5 ticks are quote updates |
| volume per trade | **no** - `last`, `volume`, `volume_real` read 0 or a constant 5000 |

The unimplemented subscribe hooks raise `NotImplementedError`, which is the
honest answer: a client that silently returns nothing for a subscription a
strategy made is worse than one that refuses it.

**The live stream is a sample; the history is not.** The bridge polls
`symbol_info_tick` rather than being pushed to, so `poll_seconds` sets how
often a quote is observed, and a tighter interval costs the terminal without
making the stream complete. `copy_ticks_range` returns every tick the terminal
recorded, so history is where completeness lives.

**`flags` has an undocumented bit.** On a tick, a one-sided revision is
`flags in (4, 130)` - bit 128 appears set on bid-only updates and is in no
documentation we could find. Measured on EURUSD: `4` (ask only) 10.0%, `130`
(bid only) 12.8%, `134` (both) 77.2%. Testing `flags in (2, 4)` reports 10.0%
one-sided where the truth is 22.8%. Test the bits, never the value.

## Two conversions that will bite

**`ts_event` on a Nautilus bar is the bar's close.** MT5 stamps a bar with its
open. `data.py` adds the bar's length when building one and the consumer has to
undo it if it wants the open. This is not cosmetic: a consumer that keys state
on bar time and refuses non-increasing timestamps will go silent rather than be
wrong by one bar.

**`Price` and `Quantity` are fixed-precision.** The precision declared for an
instrument truncates the feed before anything downstream sees it, and the
effect scales with how small a bar's range is against its tick size. On a
variance estimator, BTC at its tradable two decimals moved a 1-minute reading
by 3e-5 relative while the 15-minute reading stayed at float noise. A volume
precision of 0 rounded 701 of 3,000 BTC volumes to zero - they run 0.28, 0.45,
0.48 - which silently flipped a downstream boolean.

Precision comes from the terminal's `digits` and `volume_step` in
`providers.py`, rather than from a default.

## Generated, not maintained here

`nautilus_mt5/` is **generated** from the copy of this adapter that a live
desk runs, by `sync.py`:

```bash
python nautilus-mt5/sync.py ../till_infinity          # regenerate
python nautilus-mt5/sync.py ../till_infinity --check   # is it stale?
```

Every module carries a header naming the source path and commit, so a stale
copy can be seen rather than suspected. Editing files under `nautilus_mt5/`
directly means the next sync silently reverts the edit - change the source.

The reason is not tidiness. Maintained by hand this copy fell four modules
behind and carried none of the fixes below, so anyone who installed it got an
adapter that **could not place an order** and nothing said so.

## Status

Written against nautilus_trader 1.231.0 on Python 3.13.

**Read this before trading it.** Until 2026-11-01 the execution path had never
placed an order, and the tests did not show it because every one of them drove
a handler or asserted on a config - none reached the framework's own call
sites or the bridge's real routes. What was wrong:

- `MarketOrderRequest.sl` has no default, and the adapter never sent one, so
  **every market order was a 422** before it reached the terminal.
- `POST /trading/order` is market-only and ignores `price`, so a **limit order
  filled at the market**. Limits go to `POST /orders/pending` now.
- `pending()` asked `GET /orders/pending`, which does not exist - it is a
  `POST` that places an order - and a 404 was read as "no resting orders".
- `generate_order_filled` was called positionally without `trade_id`, so
  **every successful fill raised `TypeError`** after `OrderAccepted` had
  already gone out.
- The reconciliation hooks were named with a leading underscore, so the
  framework's own `NotImplementedError` versions ran and a node **reconciled
  nothing** on start.
- A modify with only a target sent `sl=0`, which in MT5 **removes the stop**.
- A `PLACED` reply (10008) emitted `OrderFilled` as well as `OrderAccepted`,
  so a working limit was reported as a position.
- Retcode `0` counted as success, and the bridge returns an empty body on a
  404 - so **"no answer" read as "done"** on every write.

All of those are fixed and each has a test that fails without the fix. What is
still true: a resting limit order's fill is discovered at reconciliation
rather than as it happens, because there is no execution stream; and none of
the parsing has been checked against a live terminal, only against the bridge
repo's own models.

Reconciliation reports positions, resting orders and fills filtered by
`magic`, which is how the adapter tells its own orders from anything else on
the account - and a row of its own whose instrument will not resolve now
raises rather than being skipped, because a listing that failed is otherwise
indistinguishable from a flat account.
