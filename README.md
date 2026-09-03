# MetaTrader 5 Terminal & API

A professional, dockerized environment for MetaTrader 5, providing both a VNC-accessible desktop interface and a programmatic FastAPI interface.

## 🏗️ Architecture

This repository packages everything needed to run a reliable MT5 instance on a Linux server:

-   **MT5 Terminal (Docker)**: A Wine-based container running the MetaTrader 5 desktop client, accessible via VNC (web and client).
-   **FastAPI Service**: A modern, high-performance API for interacting with the MT5 terminal programmatically.
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
      -p 6901:6901 \
      -p 8000:8000 \
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

### Symbols & Market Data
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| GET | `/api/v1/symbols/` | List all symbols |
| GET | `/api/v1/symbols/{symbol}` | Symbol info |
| POST | `/api/v1/symbols/select/{symbol}` | Add symbol to Market Watch |
| GET | `/api/v1/symbols/ticks/{symbol}` | Current bid/ask tick |
| GET | `/api/v1/symbols/rates/from` | OHLC bars from datetime + count |
| GET | `/api/v1/symbols/rates/pos` | OHLC bars from position + count |
| GET | `/api/v1/symbols/rates/range` | OHLC bars for date range |
| GET | `/api/v1/symbols/ticks/{symbol}/from` | Tick data from datetime + count |
| GET | `/api/v1/symbols/ticks/{symbol}/range` | Tick data for date range |
| POST | `/api/v1/symbols/book/{symbol}/subscribe` | Subscribe to Level 2 depth |
| POST | `/api/v1/symbols/book/{symbol}/unsubscribe` | Unsubscribe from depth |
| GET | `/api/v1/symbols/book/{symbol}` | Get current depth snapshot |

### Trading
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| POST | `/api/v1/trading/order` | Place market order (BUY/SELL) |
| POST | `/api/v1/trading/modify-sl-tp` | Modify SL/TP on open position |
| GET | `/api/v1/trading/order_check/{symbol}` | Check if symbol is tradeable |

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

