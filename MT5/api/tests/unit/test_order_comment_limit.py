"""The `comment` length the terminal accepts, declared on the request models.

**Why this is a validation limit and not a truncation.** On 2026-11-09 a client
sent 31-character comments and *every* order it had ever placed was refused
with `Invalid "comment" argument` and `mt5_code -2` - the `MetaTrader5` package
rejecting the request before it became an order, so there was no retcode, no
deal and nothing in the terminal to find. The cause took two days to locate
because the retcode alone is `-2`, and only `mt5_msg` names the field.

Swept against a live terminal at volume 0, where the answer flips to `Invalid
volume` as soon as the comment is acceptable: 31 and 30 refused, 28, 25 and 20
accepted. 29 was not tested.

A comment is how a client correlates its own orders against the terminal's
deals, so shortening one silently would hand back a different identifier than
the caller sent. A 422 naming the field and the limit is something a client can
act on; `-2` is not.

These are model tests and need no terminal: the limit is on the request model,
so it is enforced before the service is reached.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.trading import MarketOrderRequest, PendingOrderRequest
from app.utils.constants import ORDER_COMMENT_MAX

#: The measured ceiling, as a literal. Comparing a model against
#: `ORDER_COMMENT_MAX` alone would pass at any value it happened to hold,
#: including the 31 that refused every order.
MEASURED = 28

MARKET = {"symbol": "EURUSD", "volume": 0.1, "order_type": "BUY", "sl": 0.0}
PENDING = {"symbol": "EURUSD", "volume": 0.1, "order_type": "BUY_LIMIT", "price": 1.1}


def test_the_declared_limit_is_the_one_that_was_measured():
    """The constant is pinned to the sweep, not to whatever it currently says."""
    assert MEASURED >= ORDER_COMMENT_MAX, (
        f"ORDER_COMMENT_MAX is {ORDER_COMMENT_MAX}; a live terminal refused 30 and 31"
    )


@pytest.mark.parametrize("model,body", [(MarketOrderRequest, MARKET), (PendingOrderRequest, PENDING)])
def test_a_comment_over_the_limit_is_refused_by_the_model(model, body):
    """Both order routes, because a client that got this wrong got it wrong on
    whichever route it used, and the pending model carried its own default."""
    with pytest.raises(ValidationError) as caught:
        model(**body, comment="A" * (MEASURED + 1))
    assert "comment" in str(caught.value)


@pytest.mark.parametrize("model,body", [(MarketOrderRequest, MARKET), (PendingOrderRequest, PENDING)])
def test_a_comment_at_the_limit_and_an_empty_one_are_both_accepted(model, body):
    """**The control.** Without it, "long comments are refused" cannot be told
    from "the field is unusable" - and an empty comment is the common case,
    since most callers send none at all.
    """
    assert model(**body, comment="A" * ORDER_COMMENT_MAX).comment == "A" * ORDER_COMMENT_MAX
    assert model(**body).comment == ""


def test_the_real_client_order_id_that_was_refused_now_fits_once_shortened():
    """The id from the refusal that named the cause, as the adapter now sends it.

    `O-20261109-164500-001-Australia 200_E580-1` is 42 characters and was sent
    whole at 31. The tail is what tells two desks apart - the head is a
    timestamp the terminal already records against the deal - so the tail is
    what a client should keep. Included with the space in `Australia 200` to
    pin that a space was *not* what the terminal objected to.
    """
    client_order_id = "O-20261109-164500-001-Australia 200_E580-1"
    assert len(client_order_id) > ORDER_COMMENT_MAX
    kept = client_order_id[-ORDER_COMMENT_MAX:]
    assert MarketOrderRequest(**MARKET, comment=kept).comment == kept
    assert " " in kept
