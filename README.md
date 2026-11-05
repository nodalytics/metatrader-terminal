# MetaTrader 5 Terminal & API

A professional, dockerized environment for MetaTrader 5, providing both a VNC-accessible desktop interface and a programmatic FastAPI interface.

## 🏗️ Architecture

This repository packages everything needed to run a reliable MT5 instance on a Linux server:

-   **MT5 Terminal (Docker)**: A Wine-based container running the MetaTrader 5 desktop client, accessible via VNC (web and client).
-   **FastAPI Service**: A modern, high-performance API for interacting with the MT5 terminal programmatically.
-   **WebSocket Stream**: `/api/v1/stream` pushes quotes, positions and account state **as they change**, so a client is not blind between polls. MT5 has no push API, so one loop inside the process that already holds the terminal connection does the polling and sends only differences. See [Streaming](#streaming).
-   **Nginx Proxy**: Pre-configured proxy settings for handling SSL, WebSockets, and subdomain routing.
-   **CI/CD**: GitHub Actions workflows for automated Docker builds and remote EC2 deployment.

## 🔌 Reaching the terminal from a remote trading box

The trading service runs in Docker on another instance and reaches this
terminal at `host.docker.internal:8000`. **Nothing on that instance listens on
8000** — the port is held open from *this* side by a reverse SSH tunnel, and
the container can see it because the forward is bound to the Docker bridge
address rather than to loopback:

```
this host :8000  --ssh-->  remote 172.17.0.1:8000  -->  container
```

`scripts/reverse-tunnel.sh` holds the tunnel, `scripts/mt5-tunnel.service`
keeps it up, and `scripts/install-tunnel.sh` installs both:

```bash
sudo REMOTE=ubuntu@1.2.3.4 KEY=/home/mt5/.ssh/id_ed25519 ./scripts/install-tunnel.sh
```

Two things that are easy to get wrong and hard to see afterwards:

- **The remote sshd must allow a non-loopback forward**, which is off by
  default. Use `GatewayPorts clientspecified` — it permits exactly the bind
  address the client asks for. `yes` binds every forward to all interfaces,
  which on a public instance means an open port to a trading terminal. Without
  it the forward silently falls back to loopback and the container sees
  nothing, which looks like a broken bridge rather than a misconfigured sshd.
- **Verify from the remote, not from here.** A tunnel bound to the wrong
  address is indistinguishable from a working one on this side. On the remote,
  `ss -tlnp | grep 8000` should show an `ssh` process on the bridge address —
  and `curl 127.0.0.1:8000` answering nothing there is *correct*.

## 📁 Repository Structure

```text
├── .github/workflows/  # CI/CD pipelines (Build/Test & Deploy)
├── MT5/                # Dockerized MT5 Terminal & FastAPI code
├── docs/               # In-depth setup and CI/CD documentation
├── nginx/              # Nginx site configurations and snippets
├── Server/             # Legacy/Development server logic
└── README.md           # You are here
```

## 🚀 Getting Started

### Prerequisites
- Docker and Docker Compose installed.
- (Optional) Nginx for production routing.
- **On ARM hosts** (Apple Silicon, AWS Graviton): amd64 emulation. Run
  `./scripts/arm-preflight.sh` first.

> [!IMPORTANT]
> **This image is amd64, and there is no native ARM build to make.**
> MetaTrader 5 is an x86-64 *Windows* binary, and WineHQ publishes no arm64
> packages — so an arm64 image would need an x86 emulation layer inside it as
> well as Wine, with no guarantee the terminal's GUI survives.
>
> On ARM the amd64 image runs under QEMU instead. That works, and it is slower:
> expect the VNC desktop to feel sluggish, while the API stays responsive.
> `docker-compose.yml` pins `platform: linux/amd64` so the pull resolves
> instead of failing with "no matching manifest", and
> `scripts/arm-preflight.sh` checks emulation is registered — without it the
> container starts and dies with `exec format error`, which names neither the
> cause nor the fix.

### Quick Start (Local)

1.  **Clone the Repo**:
    ```bash
    git clone https://github.com/nodalytics/metatrader-terminal.git
    cd metatrader-terminal
    ```

2.  **Environment Setup**:
    Copy the example environment file and fill in your MT5 credentials:
    ```bash
    cp MT5/.env.example .env
    ```

3.  **Launch**:

    **With Docker Compose:**
    ```bash
    docker compose -f MT5/docker-compose.yml --env-file .env up -d
    ```

    **With Docker (standalone):**
    ```bash
    docker run -d \
      --name mt5-terminal \
      -p 127.0.0.1:6901:6901 \
      -p 127.0.0.1:8000:8000 \
      -e MT5_LOGIN=12345678 \
      -e MT5_PASSWORD=your_password \
      -e MT5_SERVER=YourBroker \
      -e VNC_PASSWORD=password \
      ghcr.io/nodalytics/mt5-terminal:latest
    ```

4.  **Access**:
    - **MT5 VNC (Web)**: `http://localhost:6901` (User: `mt5_user`, Pass: `password`)
    - **FastAPI Docs**: `http://localhost:8000/docs`

> [!TIP]
> **Auto-Login**: When `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER` are set, the terminal automatically logs in to your MT5 account on startup via VNC automation. The FastAPI server starts after login is verified. You can watch the process live at the VNC URL above.

> [!NOTE]
> **Startup Time**: The full startup (VNC + MT5 launch + auto-login + API) takes approximately **2 minutes**. Most of this time is the MT5 terminal connecting to your broker's server. The API will not be available until login is verified.

## 📡 API Endpoints

All endpoints (except auth, health, and docs) require an `X-API-Key` header. Get your key via `POST /api/v1/auth/login`.

### Terminal & Account
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| GET | `/api/v1/terminal/info` | Terminal info (build, connected, trade_allowed) |
| GET | `/api/v1/terminal/account/info` | Account info (balance, equity, margin) |
| GET | `/api/v1/terminal/version` | MT5 version |
| POST | `/api/v1/terminal/connect` | Connect with credentials |
| POST | `/api/v1/terminal/disconnect` | Disconnect from terminal |
| GET | `/api/v1/terminal/ping` | Broker ping latency |
| GET | `/api/v1/terminal/algo-trading` | Is AutoTrading on? (`trade_allowed`) |
| POST | `/api/v1/terminal/algo-trading` | Set AutoTrading. Idempotent - `{"enabled": true}` |

#### AutoTrading, and why it needs an endpoint

`MetaTrader5` can read `terminal_info().trade_allowed` and cannot change it: the
Algo Trading switch is a GUI control with no programmatic setter. So this
endpoint sends Ctrl+E over VNC, and everything interesting about it is in the
guard rails:

* **It is a setter, not a toggle.** It reads the current state and presses only
  on a genuine mismatch, so calling it twice is the same as calling it once and
  it is safe in a deploy step. An earlier start-up path pressed Ctrl+E three
  times on an unknown state - an odd number, so it guaranteed a net change from
  wherever it started, which is how AutoTrading came to be *off*.
* **It will not press on an unknown state.** If `terminal_info()` cannot answer,
  that is not "off"; it returns 409 rather than guessing.
* **It verifies against the terminal, not the log.** The terminal's log records
  a *transition*, so it is silent about a terminal that has never toggled - one
  incident had 20,998 log lines with no mention of trading while every order was
  rejected with 10027.

`account.trade_allowed` is a **different** flag, set by the broker. A terminal
can report `True` there and still refuse every order because this one is false.

```bash
curl -X POST localhost:8000/api/v1/terminal/algo-trading \
     -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"enabled": true}'
# {"trade_allowed": true, "changed": true, "presses": 1}
```

`assets/toggle_algo.py` does the same thing from a shell. It is duplicated
rather than shared on purpose: it is the break-glass path, and it has to work
when this service will not start.

**And the endpoint is the remedy, not the prevention.** The prevention is
`assets/ensure_algo_ini.py`, which `run-mt5.sh` now runs **before every launch**.
`common.ini` holds the persistent form of the switch, and that file was written
only inside the installer's `if [ ! -f terminal64.exe ]` branch - set once, never
reasserted. MT5 rewrites it on exit, so a terminal that ever exited with the
switch off left it off for every launch afterwards, which is exactly the
2026-09-24 outage.

That script is surgical for a reason worth knowing before editing it: a live
`common.ini` is not the four lines the installer wrote. It holds
`[Common] Environment=...`, MT5's **encrypted credential blob**, beside `Login`
and `Server`. Rewriting the file the way the installer does would take the
account's login with it, and the failure would show up as an unrelated inability
to connect. So it changes one value in place, preserves every other line
byte-for-byte, writes atomically via a temporary file in the same directory, and
keeps one backup the first time it changes anything.

The encoding is part of the contract: MT5 writes UTF-16LE with a byte-order mark
and CRLF, and silently ignores a file in any other encoding - a failure that
would look exactly like the script not working while it reported success. It
reads the file back after writing for that reason.

It runs under the container's `python3` (3.9), not Wine's, and is never fatal: a
terminal that starts with AutoTrading off is recoverable through the endpoint
above, and one that does not start is not.

#### `/health` answers three questions, not one

```json
{"status": "ok", "version": "1.0.0", "connected": true,
 "trade_allowed": true, "build": 6182}
```

`status` is about this web service and stays `ok` whenever it can answer, so a
load balancer can keep using it. A caller that needs a **tradable** terminal
must read `connected` and `trade_allowed`, because the two failures that have
actually happened here both left `status` at `ok`: a consumer polled a bridge
with no terminal attached for over a day, and a terminal with AutoTrading off
refused every order while reporting healthy.

`build` is carried because the terminal's build has broken the Python binding's
IPC before, and without it "which build was that on?" is unanswerable after the
fact. It never raises - a health route that 500s when the terminal is missing
tells you less than one that reports the terminal is missing.

### Symbols & Market Data
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| GET | `/api/v1/symbols/` | List all symbols |
| GET | `/api/v1/symbols/{symbol}` | Symbol info |
| POST | `/api/v1/symbols/select/{symbol}` | Add symbol to Market Watch |
| GET | `/api/v1/symbols/ticks/{symbol}` | Current bid/ask tick |
| GET | `/api/v1/symbols/ticks?symbols=A,B,C` | Ticks for many symbols in one call |
| GET | `/api/v1/symbols/rates/from` | OHLC bars from datetime + count |
| GET | `/api/v1/symbols/rates/pos` | OHLC bars from position + count |
| GET | `/api/v1/symbols/rates/range` | OHLC bars for date range |
| GET | `/api/v1/symbols/ticks/{symbol}/from` | Tick data from datetime + count |
| GET | `/api/v1/symbols/ticks/{symbol}/range` | Tick data for date range |
| POST | `/api/v1/symbols/book/{symbol}/subscribe` | Subscribe to Level 2 depth |
| POST | `/api/v1/symbols/book/{symbol}/unsubscribe` | Unsubscribe from depth |
| GET | `/api/v1/symbols/book/{symbol}` | Get current depth snapshot |

> [!WARNING]
> **Do not poll `/symbols/{symbol}` for a quote.** `symbol_info` returns the
> symbol's *specification*, and the `bid`/`ask` on it is a snapshot the terminal
> refreshes on its own terms. Measured 2026-11-04: eight reads three seconds
> apart returned the same bid, the same ask **and the same `time`**, while
> `/symbols/ticks/{symbol}` moved on every one.
>
> A client that polled the specification published **7 quotes from 2,399
> polls** - one per symbol, and then nothing, because its de-duplication was
> correctly suppressing a price that never changed. Nothing raised and nothing
> warned; the feed simply read as a market nobody was quoting.
>
> For a sampled quote use **`/symbols/ticks/{symbol}`** (`symbol_info_tick`,
> the latest tick). For a complete one use the **EA push** below - a poll is a
> sample, and two ticks inside one interval arrive as the later of them.

### Trading
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| POST | `/api/v1/trading/order` | Place market order (BUY/SELL) |
| POST | `/api/v1/trading/modify-sl-tp` | Modify SL/TP on open position |
| GET | `/api/v1/trading/order_check/{symbol}` | Check if symbol is tradeable |

> [!IMPORTANT]
> **`type_filling` is a property of the symbol, not a preference.** Read
> `filling_mode` from the symbol and pick from it: bit 1 is `FOK`, bit 2 `IOC`,
> bit 4 `RETURN`. Every symbol on the Deriv demo account reports
> `filling_mode=1` - **FOK only** - and sending `IOC` returns
> `10030, Unsupported filling mode` on **every** market order. Measured
> 2026-11-04 on EURUSD: `FOK` filled at retcode 10009, `IOC` and `RETURN` were
> both refused.

### Orders (Pending)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| GET | `/api/v1/orders/` | List pending orders |
| GET | `/api/v1/orders/total` | Count pending orders |
| POST | `/api/v1/orders/pending` | Place pending order (BUY_LIMIT, SELL_STOP, etc.) with optional expiration |
| PUT | `/api/v1/orders/{ticket}` | Modify pending order (price, SL, TP, expiration) |
| DELETE | `/api/v1/orders/{ticket}` | Cancel pending order |
| GET | `/api/v1/orders/calc/margin` | Calculate required margin |
| GET | `/api/v1/orders/calc/profit` | Calculate potential profit/loss |

### Positions
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| GET | `/api/v1/positions/` | List open positions |
| GET | `/api/v1/positions/by_symbol/{symbol}` | Positions by symbol |
| POST | `/api/v1/positions/close` | Close position (full or partial via `volume` param) |
| POST | `/api/v1/positions/close_all` | Close all positions |
| POST | `/api/v1/positions/modify` | Move a stop or target, **by ticket** |

> [!IMPORTANT]
> **`/positions/close` and `/positions/close_all` take query parameters, not a
> JSON body.** They declare `ticket`, `volume` and `type_filling` as plain
> function arguments with no request model, so a body gives
> `422 field required` for `query.ticket`. They are the only write routes shaped
> that way: `/trading/order` takes `MarketOrderRequest`, `/positions/modify`
> takes `ModifySLTPRequest` and `/orders/pending` takes `PendingOrderRequest`.

> [!NOTE]
> `GET /api/v1/positions/?magic=...` filters to positions carrying that magic
> number, which is how a bot finds its own trades and leaves everything else
> alone. A magic of `0` is a real value — it is what a hand-placed trade
> carries — and is filtered for, not treated as "no filter".
>
> `POST /positions/modify` takes the MT5 ticket. The older
> `/trading/modify-sl-tp` takes a `trade_id` from this service's own database
> and so cannot touch a position it did not record; it remains for callers
> already using it.

### Streaming

| Protocol | Endpoint | Description |
| :--- | :--- | :--- |
| WS | `/api/v1/stream` | Quotes, positions and account, pushed as they change |

**The URL depends on how you are reaching the terminal**, and the three forms
are not interchangeable:

| From | URL |
| :--- | :--- |
| the same host | `ws://localhost:8000/api/v1/stream` |
| through the Nginx proxy | `wss://mt5-api.yourdomain/api/v1/stream` |
| a remote box over the reverse tunnel | `ws://host.docker.internal:8000/api/v1/stream` |

The proxy form is `wss://`, not `ws://` — `snippets/proxy_params.conf` already
carries the `Upgrade` and `Connection` headers the handshake needs, so the
socket works over TLS with no extra configuration. The tunnel form is the one a
trading service on another instance uses; nothing on that instance listens on
8000, and [the section above](#-reaching-the-terminal-from-a-remote-trading-box)
explains why it still resolves.

Query parameters, all optional except the key: `api_key`, `symbols` (comma
separated), `magic` to filter positions to one bot's trades, and `interval` for
the quote loop in seconds.

```bash
wscat -c "ws://localhost:8000/api/v1/stream?api_key=KEY&symbols=XAUUSD,BTCUSD&magic=777701"
```

```jsonc
<- {"type": "ready",  "symbols": ["BTCUSD", "XAUUSD"], "interval": 0.25}
<- {"type": "tick",   "symbol": "XAUUSD", "bid": 4400.0, "ask": 4400.5, ...}
<- {"type": "positions", "positions": [ ... ]}
<- {"type": "account",   "equity": 10002.5, "balance": 10000.0, ...}

-> {"action": "subscribe",   "symbols": ["EURUSD"]}
-> {"action": "unsubscribe", "symbols": ["BTCUSD"]}
-> {"action": "ping"}
```

**Why it exists.** REST is the wrong shape for a price feed: a client learns
things at the rate it asks rather than the rate they change, is blind between
polls, and spends most round trips discovering nothing has moved.

MT5 has no push API — `symbol_info_tick` is a question, not a subscription — so
this still polls. What changes is that *one* loop inside the process already
holding the terminal connection does it, and **only differences are sent**. A
quiet symbol costs nothing; a busy one arrives as fast as it moves. Positions
and the account are polled on a slower loop and sent when the set changes,
which is what makes a server-side stop-out visible: it produces no message
anywhere, the position simply stops being there.

Floating profit is deliberately excluded from the change comparison. It moves
on every tick, and including it would turn the slow loop into a second quote
feed.

Authentication takes the same key as the REST routes, from the `X-API-Key`
header or the `api_key` query parameter — the query form because browsers
cannot set headers on a WebSocket handshake.

### History
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| GET | `/api/v1/history/deals` | Trade deal history |
| GET | `/api/v1/history/orders` | Order history |
| GET | `/api/v1/history/order_by_ticket/{ticket}` | Single order by ticket |

> [!IMPORTANT]
> **These routes spell the range `from_date` / `to_date`**, while
> `/symbols/ticks/{symbol}/range` and `/symbols/rates/from` spell it
> `date_from` / `date_to`. FastAPI drops an unrecognised query parameter
> silently and `HistoryService` then substitutes **`now - 30 days`**, so a
> client using the symbol spelling asked for one hour and received a month,
> with a `200` and nothing to indicate the window had been ignored. Measured
> 2026-11-04: 0 deals for the correct spelling against **4,013** for the wrong
> one.
>
> Both spellings are now accepted and an explicit `from_date` wins. An omitted
> range is **30 days**, not all history, and the 30-day payload on
> `/history/orders` is about 1MB and has come back unparseable once.

> [!TIP]
> Full interactive docs with request/response schemas are available at `/docs` (Swagger UI) once the API is running.

## 🔧 How It Works

### Docker Build

The image is built in cached layers, ordered by change frequency so most code changes rebuild in seconds:

1. **Base image** (`tobix/pywine:3.9`) + system deps (VNC, nginx, supervisor)
2. **MT5 terminal install** — downloads and installs MetaTrader 5 under Wine 7.0 (cached unless `run-mt5.sh` changes)
3. **Wine 10.0 upgrade** — upgrades Wine runtime and sets Windows 11 version for MT5 IPC compatibility
4. **Python dependencies** — `pip install` under Wine 10.0 (cached unless `requirements.txt` changes)
5. **Application code** — copies auto-login script, API code, configs (rebuilds instantly on any code change)

### Container Runtime

```
entrypoint.sh
  ├─ Clean stale state (X11 locks, login marker, algo trading config)
  ├─ vnc-auth.sh (set VNC password)
  └─ supervisord
       ├─ Priority 0: VNC server + noVNC web proxy
       ├─ Priority 1: Nginx, Openbox (window manager)
       ├─ Priority 2: MT5 terminal (wine terminal64.exe)
       ├─ Priority 3: Auto-login (VNC login → enable algo trading → dismiss LiveUpdate)
       └─ Priority 4: FastAPI server (connects to MT5 via IPC pipe)
```

All heavy work (pip, MT5 install, Wine upgrade) happens at **build time**. At runtime, it's just starting processes — no installs, no upgrades.

> [!NOTE]
> **LiveUpdate**: MT5 may download component updates on startup (~60s after launch). The auto-login script waits for and dismisses the LiveUpdate popup before signaling the API server to connect. This prevents the modal popup from blocking the IPC pipe.

## 📖 Documentation

For production setups, please refer to the detailed guides in the `docs/` folder:

-   [Server Setup Guide](docs/server-setup.md): Preparing your Linux/EC2 instance and Nginx.
-   [CI/CD Pipeline Setup](docs/github-actions-setup.md): Connecting GitHub Actions for automated deployments.

## 🔌 Where the terminal is: in-process or over rpyc

`MT5_TRANSPORT` chooses how this service reaches MT5. **`inprocess` is the
default and nothing about it changes** — it imports the `MetaTrader5` package
and talks to a terminal on the same machine, which is why the API runs inside
the terminal's container: the package is win32-only.

`rpyc` proxies a terminal elsewhere. It is **not faster** — an in-process call
has no network hop to remove, so routing it through rpyc adds one. What it buys
is *where the API may run*: outside the Wine container, or one API against
several terminals. If latency is what you want, the lever is
`MT5/ea/TillTickFeed.mq5`: `OnTick` is a real callback, so an EA pushes **every**
tick where a poll is a sample.

### Running it

The terminal side is a service of its own, started **beside the terminal** —
inside the Wine container, where the win32-only `MetaTrader5` package can
import:

```sh
MT5_RPYC_PORT=18812 python -m app.services.terminal_server
```

The API then runs anywhere with `MT5_TRANSPORT=rpyc`. `MT5_RPYC_BIND` chooses
the interface it listens on and defaults to loopback.

### Nothing crosses as a reference

rpyc sends primitives and tuples of primitives **by value** and everything else
— `dict` and `list` included — as a *netref*: a proxy whose every attribute
read is another round trip. Values in both directions are therefore encoded as
tagged tuples of primitives (`app/services/terminal_wire.py`) and rebuilt
locally, and an unknown shape raises rather than degrading into a proxy.

That is a structural property, not a policy setting, and it is what lets both
ends keep rpyc's restrictive defaults — `allow_all_attrs` and `allow_pickle`
off. **A tighter policy would not have been enough**: `__class__` and
`__call__` are on rpyc's safe list in every configuration, so any reference
handed over is a foothold. Measured under the restrictive defaults, the far
side reached `object.__subclasses__()` from a plain `dict` and called a
constructor that ran in the process that handed the reference over.

The terminal-side service exposes three methods — list the functions, call one,
read a constant — over a **closed list** of the functions the API actually
calls, with constants matched by name pattern. So the reachable surface is not
even "call any function on the `MetaTrader5` module", which matters because
`initialize(path)` *launches* the executable it is handed: an open module is a
code-execution primitive, not merely a way to place trades. The list is pinned
by a test that re-derives it from the application's own source, so a new call
site cannot silently find itself refused on this path alone.

`initialize` and `login` are answered with **this host's** path and
credentials whatever the client sends — they describe the terminal's machine,
which the client is by construction not on. The client's intent is kept:
whether to send credentials at all, which `connector.py` depends on because
re-sending them raises an "account changed" event that disables algo trading.

`MT5_RPYC_SECRET` adds a handshake, compared in constant time, before rpyc's
own protocol. Loopback is not an identity — every process on the host can
reach the port, and the one that reaches it can trade. It crosses in clear, so
it answers "which local process" and not "which machine"; a non-loopback bind
is refused without it.

### What it still is

**Loopback unless you say otherwise.** The channel is plaintext and
unauthenticated and whoever reaches it can place trades, so a non-local
`MT5_RPYC_HOST` — or a non-local `MT5_RPYC_BIND` — is refused without
`MT5_RPYC_ALLOW_REMOTE=1`. Reach a remote terminal through an **SSH tunnel** to
`127.0.0.1`, which is how the rest of this stack reaches one, or use
`rpyc.ssl_connect` with mutual TLS. Binding this to a public interface is
equivalent to publishing the trading password.

## 🛠️ Environment Variables

The project uses the following key variables in your `.env`:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `VNC_USER` | Username for VNC access | `mt5_user` |
| `VNC_PASSWORD` | Password for VNC access | `password` |
| `MT5_LOGIN` | Your MT5 account login number | `0` |
| `MT5_PASSWORD` | Your MT5 trading password | - |
| `MT5_SERVER` | Your broker's server name (e.g. `Deriv-Demo`) | - |
| `MT5_API_PORT` | Port for the FastAPI service | `8000` |
| `API_KEY_SEED` | Seed for API key authentication | - |
| `MT5_SETUP_URL` | Installer to fetch, for pinning a terminal build | MetaQuotes' *latest* |
| `EA_FEED_ENABLED` | Listen for `TillTickFeed.mq5`, so the stream pushes instead of polling | `true` |
| `EA_FEED_HOST` | Where that listener binds. **Loopback.** | `127.0.0.1` |
| `EA_FEED_PORT` | Its port, which the EA's `Port` input must match | `18823` |

### Pinning a terminal build

`MT5_SETUP_URL` is unset by default, so the image installs whatever MetaQuotes
currently publishes. That is the right default: a build pinned a year ago is a
build a broker may refuse, and being unable to install is worse than installing
something new.

It is overridable because a bad terminal build is a real failure mode - one
build broke the Python binding's IPC where the previous one was fine - and
testing that hypothesis means installing a specific version. Point
`MT5_SETUP_URL` at a versioned installer to do it.

**The pin holds.** `run-mt5.sh` writes `LiveUpdateMode=2` before the terminal's
first launch, so it will not upgrade itself out from under the pin. And whatever
is installed, `/health` reports `build`, so the version behind a regression is
answerable from the data rather than from memory.

## 📨 Pushing ticks instead of asking for them

`/api/v1/stream` polled for everything, and said so in its own docstring: "MT5
has no push API, so this polls." That is true of the **Python** side -
`symbol_info_tick` is a question and there is no callback to register - and it
is not true inside the terminal, where `OnTick` is a real callback.

So the push starts there. `MT5/ea/TillTickFeed.mq5` connects out to a listener
in this container and sends one line of JSON per tick; `/api/v1/stream` relays
those frames to its consumers and keeps polling for every symbol no EA is
feeding.

**Why this matters more than latency.** A poll is a sample: a quote that moved
and moved back between two polls never happened as far as a consumer is
concerned, and a tighter interval still samples - it just costs the terminal
more. A push carries every tick the terminal saw, and the EA's `seq` counter
makes a *lost* frame visible, so a consumer can refill the window from
`copy_ticks_range` instead of silently having a hole.

### Setting it up

1. Open MetaEditor in the container's VNC session, compile
   `MT5/ea/TillTickFeed.mq5`.
2. Tools → Options → Expert Advisors → allow `127.0.0.1`. MT5 checks that
   allow-list for `SocketConnect`, not only for `WebRequest`, and a missing
   entry is the usual reason the EA logs "cannot reach".
3. Attach it to **one chart per symbol**. `OnTick` only fires for the chart's
   own symbol, and the alternative - one EA calling `SymbolInfoTick` for others
   on a timer - is polling again with extra steps.

### Checking it

**Three things can silently not be true** and they look identical from a
consumer's side: the EA may not be compiled, the chart may not be open, or
`127.0.0.1` may not be in Tools -> Options -> Expert Advisors - which MT5
checks for `SocketConnect`, not only for `WebRequest`. In every case a symbol
is quietly polled instead of pushed.

```bash
curl -s localhost:8000/feed
# {"enabled":true,"listening":"127.0.0.1:18823","received":48213,
#  "gaps":0,"dropped":0,"subscribers":1,"symbols":["EURUSD","XAUUSD"]}
```

`symbols` is the answer: a name appears there only once a frame has actually
arrived for it, and leaves after two minutes of silence. An empty list with
`enabled: true` means nothing connected. `gaps` is the question after that -
frames the EA stamped that did not arrive, each one a tick a consumer has to
refill from `copy_ticks_range`.

Unprotected, like `/health` and for the same reason: a probe should not need a
credential to say whether the thing is working, and it carries no account
information.

### Reading it

Every frame carries `source`, `ea` or `poll`, so a consumer never has to infer
which feed it is on. A client that must have the push asks for it:

```
ws://host:8000/api/v1/stream?symbols=EURUSD&require=ea
```

and is **refused**, naming the symbols that are missing, rather than quietly
polled. A consumer that asked for a push and silently got a sample would take
every measurement on a feed that is not the one it believes it has, and nothing
about the result would look wrong.

`flags` and `time_msc` are passed through untouched **on the push path**. MT5's
tick flags carry an undocumented bit 128, so a one-sided revision is
`flags in (4, 130)` and never `flags in (2, 4)`; and `time_msc` is broker
server time, which is not UTC on any broker - both are the consumer's to
interpret, where it can be tested.

**The polled routes are not untouched, and the difference has cost a desk every
bar it should have received.** `/symbols/rates/*` and `/symbols/ticks/*` build a
DataFrame and run `pd.to_datetime` over `time` (and `time_msc` on ticks) before
`to_dict`, so those fields serialise as **ISO strings** - `"2026-11-03T20:15:00"`
- where the EA push sends the terminal's integer. A client that assumes MT5's
native epoch, as one reasonably would from the paragraph above, raises
`invalid literal for int() with base 10: '2026-11-...'` on every poll. It stays
connected and healthy while doing it, so the symptom is a consumer that has
quotes and no bars.

The string is naive and is UTC, because pandas produced it from an epoch. A
consumer should accept an integer, a float and an ISO string on these fields
rather than branch on the route it called.

Returning epochs everywhere would be the cleaner API and is **not** done here:
it would break any client already parsing the strings. Documented instead, so
the choice is visible.
`last`, `volume` and `volume_real` are sent and are useless on this feed, an
MT5 tick being a quote update: they read 0 or a constant 5000, and they are
included so a consumer can confirm that rather than take it on trust.

## ✅ A reply the client can trust

Six routes used to end `{"success": True, "result": ...}` without looking at
the terminal's retcode, so the field named `success` was true on every reply
the route managed to produce. A client that moved a stop, closed a position or
cancelled an order could not learn it had been refused - the retcode was in
`result`, where only a client that remembered to look would find it.

They now raise `400` with the terminal's own comment and code, which is what
`/trading/modify-sl-tp` always did: a 4xx is handled by every HTTP client, and
a 200 carrying a buried refusal is handled only by an attentive one.

Two were worse than misleading:

- **`POST /trading/order` wrote a trade row for a rejected order.** It read
  `result.price` before any check, so a refusal (`None`) was a 500 - and when
  the terminal refused with a code instead, the row was written anyway and the
  reply said `success: true`. This service's own database carried positions
  the account never held.
- **`POST /positions/modify` had the same `None._asdict()` crash**, so a
  refused stop move and a broken service looked identical from outside.

`app/utils/sent.py` holds the reading, with the accepted codes as a named
argument: a close or a cancel wants `DONE`, a pending placement wants
`PLACED` - and also accepts `DONE`, because a limit at a price the market has
already reached fills immediately, which a `PLACED`-only check would call a
failure. Written out per route, the difference between those is a number
nobody reviewing a diff would question.

## 🧪 Tests

```bash
cd MT5/api
pip install -r requirements.txt
python -m pytest tests/unit -q      # no container, no Wine, no broker
python -m pytest tests -q           # integration; needs a running container
```

`tests/unit` runs the **real** application against a fake `MetaTrader5` module
installed into `sys.modules` before `app` is imported. Only the terminal is
replaced, so what is under test is the actual routing, serialisation and
argument passing.

That matters because the fake is **strict where the real package is strict**.
`positions_get` there refuses a `magic=` keyword exactly as MetaTrader's does,
which is what caught the position filter passing one. A permissive mock would
have accepted it and the tests would have passed while the endpoint failed.

`tests/` remains integration tests against a live container, for anything that
needs a real market.

## 🤝 Contributing

Contributions are welcome! Please fork the repository and submit a pull request.

