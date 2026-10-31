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

## Status

Written against nautilus_trader 1.221.0. The data path is exercised; the
execution path's reconciliation reports positions, resting orders and fills
filtered by `magic`, which is how the adapter tells its own orders from
anything else on the account.
