"""The HTTP client for `nodalytics/metatrader-terminal`.

**No Nautilus types cross this boundary.** It speaks the bridge's JSON and
returns plain Python, so it can be tested against a fake without a kernel, and
so a Rust rewrite of the adapter above it does not have to reimplement the
wire protocol at the same time.

Routes, as the bridge exposes them under `/api/v1`:

| route | what for |
| --- | --- |
| `GET /terminal/ping` | is the bridge up |
| `GET /terminal/account/info` | balance, equity, currency, leverage |
| `GET /symbols/` | what the terminal lists |
| `GET /symbols/info/{symbol}` | digits, point, volume step, contract size |
| `GET /symbols/rates/pos` | bars, by position back from now |
| `GET /symbols/ticks/{symbol}` | ticks over a time range |
| `GET /positions/` | open positions |
| `GET /orders/pending` | resting orders |
| `POST /trading/order` | place |
| `POST /positions/modify` | move a stop or target, by ticket |
| `POST /positions/close` | close |
| `DELETE /orders/{ticket}` | cancel a resting order |
| `GET /history/deals` | fills, for reconciliation |
"""

from __future__ import annotations

from typing import Any

import httpx

API = "/api/v1"


class BridgeError(RuntimeError):
    """The bridge answered, and the answer was a refusal."""


class Bridge:
    """A thin async client. One session, reused."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ──── lifecycle ────
    async def open(self) -> None:
        """Create the session. Called from `_connect`, not the constructor.

        The adapter guide is explicit about this: loop-bound resources belong
        in `_connect`, because the client is constructed before the node's
        event loop exists.
        """
        if self._client is not None:
            return
        headers = {"Accept": "application/json"}
        if self._key:
            headers["X-API-Key"] = self._key
        self._client = httpx.AsyncClient(
            base_url=self._base + API,
            headers=headers,
            timeout=self._timeout,
        )

    async def close(self) -> None:
        if self._client is None:
            return
        client, self._client = self._client, None
        await client.aclose()

    @property
    def session(self) -> httpx.AsyncClient:
        if self._client is None:
            raise BridgeError("the bridge session is not open - call open() first")
        return self._client

    # ──── plumbing ────
    async def get(self, route: str, **params: Any) -> Any:
        return await self._send(
            "GET", route, params={k: v for k, v in params.items() if v is not None}
        )

    async def post(self, route: str, body: dict[str, Any]) -> Any:
        return await self._send("POST", route, json=body)

    async def delete(self, route: str) -> Any:
        return await self._send("DELETE", route)

    async def _send(self, method: str, route: str, **kwargs: Any) -> Any:
        response = await self.session.request(method, route, **kwargs)
        if response.status_code == 404:
            # A 404 is "nothing there", not a failure: the tick corpus relies
            # on an empty range reading as empty rather than as an error.
            return None
        if response.status_code >= 400:
            raise BridgeError(f"{method} {route} -> {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - a bridge that is not JSON
            raise BridgeError(f"{method} {route} returned non-JSON") from exc

    # ──── reads ────
    async def ping(self) -> bool:
        return bool(await self.get("/terminal/ping"))

    async def account(self) -> dict[str, Any]:
        return (await self.get("/terminal/account/info")) or {}

    async def symbols(self) -> list[str]:
        raw = await self.get("/symbols/")
        if isinstance(raw, dict):
            raw = raw.get("symbols") or raw.get("data") or []
        return [str(s) for s in (raw or [])]

    async def symbol_info(self, symbol: str) -> dict[str, Any]:
        return (await self.get(f"/symbols/info/{symbol}")) or {}

    async def bars(self, symbol: str, timeframe: str, count: int, start: int = 0) -> list[dict]:
        """Bars by position back from now. `start=0` is the forming bar."""
        raw = await self.get(
            "/symbols/rates/pos",
            symbol=symbol,
            timeframe=timeframe,
            start_pos=start,
            count=count,
        )
        return _rows(raw)

    async def ticks(self, symbol: str, start: int, end: int) -> list[dict]:
        raw = await self.get(f"/symbols/ticks/{symbol}", date_from=start, date_to=end)
        return _rows(raw)

    async def positions(self) -> list[dict]:
        return _rows(await self.get("/positions/"))

    async def pending(self) -> list[dict]:
        return _rows(await self.get("/orders/pending"))

    async def deals(self, start: int, end: int) -> list[dict]:
        return _rows(await self.get("/history/deals", date_from=start, date_to=end))

    # ──── writes ────
    async def order(self, body: dict[str, Any]) -> dict[str, Any]:
        return (await self.post("/trading/order", body)) or {}

    async def modify(self, ticket: int, stop: float, target: float = 0.0) -> dict[str, Any]:
        body: dict[str, Any] = {"ticket": int(ticket), "sl": float(stop)}
        if target:
            body["tp"] = float(target)
        return (await self.post("/positions/modify", body)) or {}

    async def close_position(self, ticket: int, volume: float | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"ticket": int(ticket)}
        if volume is not None:
            body["volume"] = float(volume)
        return (await self.post("/positions/close", body)) or {}

    async def cancel(self, ticket: int) -> dict[str, Any]:
        return (await self.delete(f"/orders/{int(ticket)}")) or {}


def _rows(raw: Any) -> list[dict]:
    """The bridge returns a list, or a dict wrapping one, depending on route."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        for key in ("data", "rates", "ticks", "positions", "orders", "deals"):
            inner = raw.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
    return []
