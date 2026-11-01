"""What an `order_send` reply means, and the five routes that did not ask.

Six routes ended `return {"success": True, "result": result._asdict()}` with
no look at the retcode - `/trading/order`, `/positions/modify`,
`/positions/close`, `/orders/pending`, `PUT /orders/{ticket}` and
`DELETE /orders/{ticket}`. So the field named `success` was true on every
reply the route managed to produce, and a client that moved a stop, closed a
position or cancelled an order had no way to learn the terminal had refused.

`/trading/modify-sl-tp` always did it properly, which is what makes this a
consistency fix rather than a new policy.

Two of them were worse than misleading. `/trading/order` read `result.price`
before any check, so a refusal (`None`) was an `AttributeError` and a 500 -
and it still **wrote a trade row**, leaving the service's own database
carrying positions the account never held. `/positions/modify` had the same
`None._asdict()` crash, which made a refused stop move and a broken service
look identical from outside.

These run against a fake reply rather than a terminal, because what is under
test is the reading of a retcode and nothing else - the existing suite in this
directory is integration-style and needs a live container, which is the wrong
shape for this.
"""

from __future__ import annotations

from collections import namedtuple

import pytest

from app.utils.exceptions import MT5OrderError
from app.utils.sent import DONE, PLACED, answered, checked

#: The fields of `order_send`'s reply that any of this reads.
Reply = namedtuple("Reply", "retcode comment order price volume")


def reply(retcode: int, comment: str = "ok") -> Reply:
    return Reply(retcode=retcode, comment=comment, order=777, price=1.2345, volume=0.1)


# ──── accepted ────


def test_a_filled_order_is_a_success():
    body = answered(reply(10009), DONE, "buy EURUSD")
    assert body["success"] is True
    assert body["result"]["order"] == 777


def test_a_resting_order_is_a_success_for_a_placement():
    """`PLACED` is what a pending order gets, and a route that only accepted
    `DONE` would call a working limit a failure."""
    assert answered(reply(10008), PLACED, "pending")["success"] is True


def test_a_limit_that_filled_immediately_is_still_a_placement():
    """A limit at a price the market has already reached fills at once and the
    terminal reports `DONE` - the best available outcome, which a
    `PLACED`-only check would report as a failure."""
    assert answered(reply(10009), PLACED, "pending")["success"] is True


# ──── refused ────


def test_a_resting_reply_is_not_a_fill():
    """The other direction of the same asymmetry: `PLACED` means it is
    working, so a route expecting a *close* or a *cancel* must not take it."""
    with pytest.raises(MT5OrderError):
        answered(reply(10008), DONE, "close of 42")


@pytest.mark.parametrize(
    ("retcode", "comment"),
    [
        (10016, "Invalid stops"),
        (10013, "Invalid request"),
        (10027, "AutoTrading disabled"),
        (10030, "Unsupported filling mode"),
        (0, "no answer at all"),
    ],
)
def test_a_refusal_raises_rather_than_reporting_success(retcode, comment):
    """**The bug.** Every one of these answered `success: true`, with the
    retcode buried in `result` where only a client that remembered to look
    would find it.

    `0` is in here because it is what an empty reply reads as, and the
    adapter's own `_OK_RETCODES` used to include it - so "no answer" counted
    as "done" on both sides of the wire at once.
    """
    with pytest.raises(MT5OrderError) as raised:
        answered(reply(retcode, comment), DONE, "modify of 42")
    assert str(retcode) in str(raised.value) or comment in str(raised.value)


def test_the_error_carries_the_terminals_own_words():
    """"Modify failed" sends a reader to the logs; "Invalid stops (10016)"
    tells them what to change."""
    with pytest.raises(MT5OrderError) as raised:
        answered(reply(10016, "Invalid stops"), DONE, "modify of 42")
    assert "Invalid stops" in str(raised.value)
    assert "modify of 42" in str(raised.value)


def test_a_reply_with_no_comment_still_says_something():
    """A terminal that refuses without a comment is not a reason to raise an
    error whose message is empty."""
    with pytest.raises(MT5OrderError, match="no comment"):
        answered(Reply(10016, "", 0, 0.0, 0.0), DONE, "modify")


# ──── the crash it was hiding ────


def test_nothing_sent_is_a_refusal_and_not_a_crash():
    """`order_send` returns `None` when the request never reached the server -
    a terminal that is not connected, or a request the library rejected. The
    routes called `None._asdict()`, which FastAPI turned into a 500, so a
    refused order and a broken service looked identical from outside."""
    with pytest.raises(MT5OrderError, match="was not sent"):
        answered(None, DONE, "buy EURUSD")


def test_checked_returns_the_reply_for_a_caller_with_work_left():
    """`/trading/order` reads the fill price and writes a database row, so it
    validates first and builds its own body - where `answered` would already
    have built one."""
    found = checked(reply(10009), DONE, "buy EURUSD")
    assert found.price == 1.2345


def test_checked_refuses_before_a_caller_can_use_the_reply():
    """Which is the whole point of its existing: the route it serves used to
    read `result.price` immediately, so a rejected order wrote a trade row
    before anything noticed."""
    with pytest.raises(MT5OrderError):
        checked(reply(10016, "Invalid stops"), DONE, "buy EURUSD")
    with pytest.raises(MT5OrderError):
        checked(None, DONE, "buy EURUSD")


def test_the_two_entry_points_agree_about_a_refusal():
    """`answered` delegates to `checked`, so they cannot come to disagree
    about what a refusal is - which they would, written out twice."""
    for bad in (None, reply(10016), reply(0)):
        with pytest.raises(MT5OrderError):
            checked(bad, DONE, "x")
        with pytest.raises(MT5OrderError):
            answered(bad, DONE, "x")
