"""The Deriv/MT5 venue, as a Nautilus adapter.

Nautilus has no MT5 integration and the community ones are unofficial,
Windows-bound and of unknown maintenance, so this is ours. It talks to
`nodalytics/metatrader-terminal` - MT5 under Wine behind an HTTP/WS API - which
means the terminal stays on the lab box and this runs anywhere.

**Laid out like a Nautilus adapter crate on purpose.** `config.py`, `data.py`,
`execution.py`, `factories.py` are the same files their Rust adapters carry as
`config.rs`, `data.rs`, `execution.rs`, `factories.rs`. If this is rewritten in
Rust - which is where their adapter work is going - it is a translation rather
than a redesign.

**What this feed can and cannot do**, measured on the terminal rather than
assumed from the API surface:

- quotes and bars, which is what `LiveMarketDataClient` gets here
- **no order book**: `market_book_add` fails outright
- **no trade ticks**: MT5 ticks are quote updates, and `last`, `volume` and
  `volume_real` read 0 or a constant 5000
- `flags` is a bitfield with an undocumented bit 128, so a one-sided revision
  is `flags in (4, 130)` and never `flags in (2, 4)` - which under-reports
  one-sidedness as 10.0% where the truth is 22.8%

The unimplemented hooks are left to raise `NotImplementedError`, which is the
honest answer for a feed that does not carry them.
"""

from .config import MT5DataClientConfig, MT5ExecClientConfig
from .factories import MT5LiveDataClientFactory, MT5LiveExecClientFactory

MT5 = "MT5"

__all__ = [
    "MT5",
    "MT5DataClientConfig",
    "MT5ExecClientConfig",
    "MT5LiveDataClientFactory",
    "MT5LiveExecClientFactory",
]
