"""What MT5's trade server answered, in words a caller can act on.

Every write route on the bridge ends in `order_send`, and `order_send`
answers with an integer. `10027` is the whole of what a refused order says
about itself, and a consumer that only records the number records nothing: the
code is the difference between "the market is closed", "your volume is wrong"
and "a button in the terminal GUI is off", and those have three different
remedies.

So each result carries both. The raw code stays, because it is the only thing
that is stable and the only thing worth matching on; the sentence beside it is
what makes the raw code legible without a lookup table in the reader's head.

The text is deliberately shorter and blunter than `RETCODE_DESCRIPTIONS` in
`MT5/api/app/utils/constants.py`, which is the terminal's own wording. "Invalid
stops in the request" does not say that the stop was too close to the market;
`ACTION` below does. The codes themselves are MQL5's and are not duplicated
from any running process, so this module imports nothing and can be read
anywhere.
"""

from __future__ import annotations

#: The two codes that mean the request succeeded.
#:
#: Two rather than one because a *resting* order succeeds by resting - `10008`
#: - while a fill succeeds by filling - `10009`. A limit placed at a price the
#: market has already reached fills immediately and answers `10009`, which is
#: the best available outcome and which a `10008`-only reading would report as
#: a failure. `MT5/api/app/utils/sent.py` makes the same distinction on the
#: bridge's side.
FILLED = 10009
PLACED = 10008
SUCCESS = (FILLED, PLACED)

#: Code -> (one-line meaning, what the caller can do about it).
#:
#: The second half is the point. A trading agent reading `10030` needs to know
#: that the fix is a different `type_filling`, not a different price, and
#: nothing in the number says so.
MEANINGS: dict[int, tuple[str, str]] = {
    10004: ("requote", "the price moved while the order was in flight; re-read the quote and resubmit"),
    10006: ("rejected by the dealer", "the broker refused it; nothing in the request is necessarily wrong"),
    10007: ("cancelled by the trader", "no action"),
    10008: ("placed and resting", "the order is live and waiting; it has not filled"),
    10009: ("done", "filled, closed or modified as asked"),
    10010: ("partially filled", "only part of the volume was executed; read the result volume, not the request"),
    10011: ("processing error", "retry; if it repeats, the terminal is the suspect, not the order"),
    10012: ("timed out", "the outcome is unknown until positions and history are re-read"),
    10013: ("invalid request", "a field is malformed or missing for this order type"),
    10014: ("invalid volume", "round the volume to the symbol's volume_step and keep it within volume_min..volume_max"),
    10015: ("invalid price", "the price is not a valid level for this symbol; check digits and point"),
    10016: ("invalid stops", "the stop or target is closer to the market than the symbol's trade_stops_level allows"),
    10017: ("trading disabled", "the terminal or the account is not permitted to trade at all"),
    10018: ("market closed", "wait for the session; this is not a defect in the order"),
    10019: ("not enough money", "reduce volume, or read margin_free from the account before sizing"),
    10020: ("prices changed", "re-read the quote and resubmit"),
    10021: ("no quotes", "the symbol is not quoting; it may not be in Market Watch"),
    10022: ("invalid expiration", "the expiration is in the past or not allowed for this type_time"),
    10023: ("order state changed", "the order was filled or cancelled between reading it and acting on it"),
    10024: ("too many requests", "slow down; the terminal is rate limiting"),
    10025: ("no changes", "the modify asked for values the order already has"),
    10026: ("AutoTrading disabled by the broker", "the server, not the terminal, is refusing; nothing local will fix it"),
    10027: (
        "AutoTrading disabled in the terminal",
        "the AutoTrading button in the MT5 GUI is off; POST /api/v1/terminal/algo-trading turns it on. "
        "account_status reports this as autotrading_enabled",
    ),
    10028: ("locked for processing", "an earlier request on this order is still being handled"),
    10029: ("order or position frozen", "the broker's freeze level forbids changing it this close to the market"),
    10030: (
        "unsupported filling mode",
        "the symbol does not permit the type_filling that was sent; read filling_allowed from instrument_spec",
    ),
    10031: ("no connection to the trade server", "the terminal is up but the broker is not reachable"),
    10032: ("live accounts only", "this operation is not available on a demo account"),
    10033: ("pending order limit reached", "cancel a resting order before placing another"),
    10034: ("symbol volume limit reached", "the account already holds the maximum volume this symbol allows"),
    10035: ("prohibited order type", "this order type is not allowed for this symbol"),
    10036: ("position already closed", "re-read open positions; the ticket no longer refers to anything"),
    10038: ("close volume exceeds the position", "a partial close cannot ask for more than the position holds"),
    10039: ("a close order already exists", "an exit for this position is already working"),
    10040: ("open position limit reached", "the account is at its maximum number of simultaneous positions"),
    10041: ("pending order activation rejected", "the order was cancelled rather than triggered"),
    10042: ("long only", "the symbol permits buys only"),
    10043: ("short only", "the symbol permits sells only"),
    10044: ("close only", "the symbol permits closing, not opening"),
    10045: ("FIFO rule", "this account must close the oldest position in the symbol first"),
}


def explain(code: int | None) -> dict[str, object]:
    """A retcode as `{retcode, retcode_meaning, remedy, ok}`.

    `ok` is computed from the code rather than taken from the bridge's own
    `success` field, which is `true` on every response the bridge manages to
    build. A caller that trusts a boolean someone else computed inherits
    whatever that someone meant by it; a caller that reads the code decides for
    itself.

    An unknown code is reported as unknown and carries the number. Silently
    mapping it to "failed" would turn a code MetaQuotes added after this file
    was written into a confident wrong answer.
    """
    if code is None:
        return {
            "retcode": None,
            "retcode_meaning": "the terminal returned no retcode",
            "remedy": "the request did not reach the trade server; the bridge's error text says why",
            "ok": False,
        }
    code = int(code)
    meaning, remedy = MEANINGS.get(code, (f"unrecognised retcode {code}", "consult the MQL5 retcode table"))
    return {
        "retcode": code,
        "retcode_meaning": meaning,
        "remedy": remedy,
        "ok": code in SUCCESS,
    }
