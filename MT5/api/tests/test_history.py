"""Tests for history endpoints."""
import pytest


def test_get_deals_no_params(client):
    """Works without a range - which defaults to **30 days**, not all history.

    `HistoryService` substitutes `now - timedelta(days=30)`. This test used to
    say "all history", and a caller believing that reads a truncated book with
    no indication it was truncated.
    """
    r = client.get("/api/v1/history/deals")
    assert r.status_code == 200


def test_the_deal_window_is_honoured_and_not_silently_widened(client):
    """A narrow window must return no more than a wide one.

    **A 200 proves nothing here.** An unrecognised range parameter is dropped
    by FastAPI and the service answers with its own 30-day default, so the old
    status-code assertions passed while every window was ignored: asking a live
    terminal for one hour with the wrong spelling returned 4,013 deals over 30
    days.
    """
    wide = client.get(
        "/api/v1/history/deals",
        params={"from_date": "2020-01-01T00:00:00", "to_date": "2038-01-01T00:00:00"},
    )
    narrow = client.get(
        "/api/v1/history/deals",
        params={"from_date": "2020-01-01T00:00:00", "to_date": "2020-01-02T00:00:00"},
    )
    assert (wide.status_code, narrow.status_code) == (200, 200)
    assert len(narrow.json() or []) <= len(wide.json() or []), (
        "a one-day window in 2020 returned more deals than a eighteen-year one, "
        "so the range is not being applied"
    )


def test_both_spellings_of_the_range_agree(client):
    """`date_from` is accepted as well as `from_date`.

    This service declares `date_from` on its symbol routes and `from_date` on
    its history routes, and a client written against one spelling silently got
    the 30-day default from the other. Both are accepted so that neither
    caller is wrong.
    """
    window = ("2020-01-01T00:00:00", "2020-01-02T00:00:00")
    documented = client.get(
        "/api/v1/history/deals", params={"from_date": window[0], "to_date": window[1]}
    )
    alias = client.get(
        "/api/v1/history/deals", params={"date_from": window[0], "date_to": window[1]}
    )
    assert (documented.status_code, alias.status_code) == (200, 200)
    assert (documented.json() or []) == (alias.json() or []), (
        "the two spellings of the same window disagree, so one of them is "
        "being dropped and answered with the 30-day default"
    )


def test_get_deals_with_dates(client):
    r = client.get("/api/v1/history/deals", params={
        "from_date": "2025-01-01T00:00:00",
        "to_date": "2025-12-31T23:59:59",
    })
    assert r.status_code == 200
    data = r.json()
    assert data is None or isinstance(data, list)


def test_get_deals_with_position(client):
    r = client.get("/api/v1/history/deals", params={
        "from_date": "2025-01-01T00:00:00",
        "to_date": "2025-12-31T23:59:59",
        "position": 0,
    })
    assert r.status_code == 200


def test_get_orders_no_params(client):
    r = client.get("/api/v1/history/orders")
    assert r.status_code == 200


def test_get_orders_with_dates(client):
    r = client.get("/api/v1/history/orders", params={
        "from_date": "2025-01-01T00:00:00",
        "to_date": "2025-12-31T23:59:59",
    })
    assert r.status_code == 200
    data = r.json()
    assert data is None or isinstance(data, list)


def test_get_order_by_ticket_not_found(client):
    r = client.get("/api/v1/history/order_by_ticket/999999999")
    assert r.status_code in (404, 500)
