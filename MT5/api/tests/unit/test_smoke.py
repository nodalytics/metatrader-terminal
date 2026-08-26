"""The app imports and answers, with no terminal anywhere."""

from __future__ import annotations


def test_the_app_starts_without_a_metatrader_installation(app_client):
    assert app_client.get("/health").status_code == 200


def test_the_symbol_list_is_served(app_client):
    response = app_client.get("/api/v1/symbols/")
    assert response.status_code == 200
    assert "XAUUSD" in response.json()
