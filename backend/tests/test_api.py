"""End-to-end API behaviour, including the failure paths."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.providers.base import MarketDataProvider, ProviderError
from app.providers.demo import DemoMarketDataProvider
from app.providers.registry import ProviderRouter, set_router


class BrokenProvider(MarketDataProvider):
    name = "broken"

    def __init__(self, kind: str = "timeout") -> None:
        self.kind = kind
        self.calls = 0

    async def get_quotes(self, symbols, at=None):
        self.calls += 1
        raise ProviderError(self.kind, "Upstream did not respond")

    async def search(self, query, limit=10):
        raise ProviderError(self.kind, "Upstream did not respond")


def make_watchlist(client, headers, name="Core", symbols=("RELIANCE", "INFY")):
    response = client.post(
        "/api/watchlists", json={"name": name, "symbols": list(symbols)}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- identity and ownership ----------------------------------------------------


def test_requests_without_a_user_key_are_rejected(client):
    response = client.get("/api/watchlists")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_user_key"


def test_malformed_user_key_is_rejected(client):
    response = client.get("/api/watchlists", headers={"X-User-Key": "short"})
    assert response.status_code == 400


def test_one_user_cannot_read_another_users_watchlist(client, headers):
    watchlist = make_watchlist(client, headers)
    other = {"X-User-Key": "another-user-key-99999"}
    response = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=other)
    assert response.status_code == 404


# --- watchlist management ------------------------------------------------------


def test_create_rename_and_delete(client, headers):
    watchlist = make_watchlist(client, headers, symbols=())
    assert watchlist["stockCount"] == 0

    renamed = client.patch(
        f"/api/watchlists/{watchlist['id']}", json={"name": "Long term"}, headers=headers
    )
    assert renamed.json()["name"] == "Long term"

    assert client.delete(f"/api/watchlists/{watchlist['id']}", headers=headers).status_code == 204
    assert client.get("/api/watchlists", headers=headers).json() == []


def test_duplicate_watchlist_name_is_a_clear_conflict(client, headers):
    make_watchlist(client, headers, symbols=())
    response = client.post("/api/watchlists", json={"name": "Core"}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_name"


def test_blank_watchlist_name_is_rejected(client, headers):
    response = client.post("/api/watchlists", json={"name": "   "}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_adding_a_duplicate_stock_is_rejected_with_an_explanation(client, headers):
    watchlist = make_watchlist(client, headers, symbols=("RELIANCE",))
    response = client.post(
        f"/api/watchlists/{watchlist['id']}/stocks", json={"symbol": "reliance"}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_stock"
    assert "already" in response.json()["error"]["message"].lower()


def test_unknown_symbol_is_rejected_rather_than_stored_empty(client, headers):
    watchlist = make_watchlist(client, headers, symbols=())
    response = client.post(
        f"/api/watchlists/{watchlist['id']}/stocks", json={"symbol": "NOTAREALTICKER"}, headers=headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_symbol"


def test_symbol_validation_rejects_injection_shaped_input(client, headers):
    watchlist = make_watchlist(client, headers, symbols=())
    response = client.post(
        f"/api/watchlists/{watchlist['id']}/stocks",
        json={"symbol": "'; DROP TABLE stocks;--"},
        headers=headers,
    )
    assert response.status_code == 422


def test_removing_a_stock_that_is_not_there(client, headers):
    watchlist = make_watchlist(client, headers, symbols=("RELIANCE",))
    response = client.delete(f"/api/watchlists/{watchlist['id']}/stocks/INFY", headers=headers)
    assert response.status_code == 404


# --- the feed ------------------------------------------------------------------


def test_empty_watchlist_returns_an_empty_feed_not_an_error(client, headers):
    watchlist = make_watchlist(client, headers, symbols=())
    body = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()
    assert body["items"] == []
    assert body["counts"]["total"] == 0
    assert body["checkpointAt"] is None


def test_first_visit_has_no_checkpoint_so_nothing_is_diffed(client, headers):
    watchlist = make_watchlist(client, headers)
    body = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()
    assert body["checkpointAt"] is None
    assert all(item["status"] == "new" for item in body["items"])
    assert body["counts"]["changed"] == 0


def test_reading_the_feed_does_not_advance_the_checkpoint(client, headers):
    watchlist = make_watchlist(client, headers)
    client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers)
    client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers)
    body = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()
    assert body["checkpointAt"] is None


def test_checkpoint_then_feed_produces_a_comparison(client, headers):
    watchlist = make_watchlist(client, headers)
    checkpoint = client.post(f"/api/watchlists/{watchlist['id']}/checkpoint", json={}, headers=headers)
    assert checkpoint.json()["advanced"] is True

    body = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()
    assert body["checkpointAt"] is not None
    assert body["awaySeconds"] is not None


def test_checkpoint_is_idempotent_for_a_given_seen_through(client, headers):
    """The client acknowledges the feed it rendered, not "now".

    Sending the rendered timestamp is what makes a double-click, a retry, and a
    second tab replaying the same request converge instead of racing.
    """
    watchlist = make_watchlist(client, headers)
    feed = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()
    payload = {"seenThrough": feed["generatedAt"]}

    results = [
        client.post(f"/api/watchlists/{watchlist['id']}/checkpoint", json=payload, headers=headers).json()
        for _ in range(4)
    ]
    assert results[0]["advanced"] is True
    assert all(r["advanced"] is False for r in results[1:])
    assert len({r["checkpointVersion"] for r in results}) == 1


def test_bare_checkpoint_press_is_monotonic(client, headers):
    watchlist = make_watchlist(client, headers)
    stamps = [
        client.post(f"/api/watchlists/{watchlist['id']}/checkpoint", json={}, headers=headers).json()[
            "checkpointAt"
        ]
        for _ in range(3)
    ]
    assert stamps == sorted(stamps)


def test_checkpoint_rejects_a_client_clock_from_the_future(client, headers):
    watchlist = make_watchlist(client, headers)
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    body = client.post(
        f"/api/watchlists/{watchlist['id']}/checkpoint",
        json={"seenThrough": future.isoformat()},
        headers=headers,
    ).json()
    assert datetime.fromisoformat(body["checkpointAt"]) < future


def test_every_flagged_item_carries_reasons_and_confidence(client, headers):
    watchlist = make_watchlist(client, headers, symbols=("RELIANCE", "INFY", "TCS", "ITC"))
    client.post("/api/demo/prepare", json={"watchlist_id": watchlist["id"]}, headers=headers)
    body = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()

    assert body["counts"]["changed"] >= 1
    for item in body["items"]:
        assert item["reasons"], f"{item['symbol']} was rendered without an explanation"
        assert 0.0 <= item["confidence"] <= 1.0
        assert item["freshness"] in {"live", "recent", "stale", "unavailable"}


def test_stale_feed_is_labelled_and_keeps_its_last_reliable_price(client, headers):
    watchlist = make_watchlist(client, headers, symbols=("ITC",))
    client.post("/api/demo/prepare", json={"watchlist_id": watchlist["id"]}, headers=headers)
    item = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()["items"][0]

    assert item["freshness"] == "stale"
    assert item["price"] is not None and item["price"] > 0
    assert "update" in item["freshnessLabel"].lower()


def test_conflicting_sources_are_surfaced(client, headers):
    watchlist = make_watchlist(client, headers, symbols=("INFY",))
    client.post("/api/demo/prepare", json={"watchlist_id": watchlist["id"]}, headers=headers)
    item = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()["items"][0]

    assert item["sourceDisagreementPct"]
    assert any(r["code"] == "source_conflict" for r in item["reasons"])


def test_long_absence_produces_a_timeline(client, headers):
    watchlist = make_watchlist(client, headers, symbols=("RELIANCE", "INFY", "TCS"))
    client.post(
        "/api/demo/prepare",
        json={"watchlist_id": watchlist["id"], "days": 8, "checkpoint_days_ago": 5},
        headers=headers,
    )
    body = client.get(f"/api/watchlists/{watchlist['id']}/feed", headers=headers).json()

    assert body["awayLabel"].endswith("days")
    assert len(body["timeline"]) >= 2
    assert all("day" in day and "changed" in day for day in body["timeline"])


# --- provider failure ----------------------------------------------------------


def test_provider_failure_falls_back_to_demo_and_says_so(client, headers):
    broken = BrokenProvider("timeout")
    set_router(ProviderRouter(primary=broken, fallback=DemoMarketDataProvider()))

    watchlist = make_watchlist(client, headers, symbols=("RELIANCE",))
    body = client.get(
        f"/api/watchlists/{watchlist['id']}/feed?force=true", headers=headers
    ).json()

    assert body["data"]["degraded"] is True
    assert body["data"]["isDemo"] is True
    assert body["data"]["errorKind"] == "timeout"
    # And the app still renders a price rather than a blank or a zero.
    assert body["items"][0]["price"] > 0


def test_repeated_failures_open_the_circuit_and_stop_hammering(client, headers):
    broken = BrokenProvider("unavailable")
    router = ProviderRouter(primary=broken, fallback=DemoMarketDataProvider())
    set_router(router)

    watchlist = make_watchlist(client, headers, symbols=("RELIANCE",))
    for _ in range(4):
        client.get(f"/api/watchlists/{watchlist['id']}/feed?force=true", headers=headers)

    assert router.circuit_open
    assert router.mode == "degraded"
    # The provider was tried a couple of times, not once per request.
    assert broken.calls <= 3


def test_rate_limited_provider_is_reported_as_such(client, headers):
    set_router(ProviderRouter(primary=BrokenProvider("rate_limited"), fallback=DemoMarketDataProvider()))
    watchlist = make_watchlist(client, headers, symbols=("RELIANCE",))
    body = client.get(f"/api/watchlists/{watchlist['id']}/feed?force=true", headers=headers).json()
    assert body["data"]["errorKind"] == "rate_limited"


def test_health_reports_provider_and_database(client):
    body = client.get("/api/health").json()
    assert body["database"] == "ok"
    assert "mode" in body["provider"]


# --- search and preferences ----------------------------------------------------


def test_search_returns_matches(client, headers):
    hits = client.get("/api/stocks/search?q=REL", headers=headers).json()
    assert any(h["symbol"] == "RELIANCE" for h in hits)


def test_search_marks_symbols_already_on_the_watchlist(client, headers):
    watchlist = make_watchlist(client, headers, symbols=("RELIANCE",))
    hits = client.get(
        f"/api/stocks/search?q=RELIANCE&watchlistId={watchlist['id']}", headers=headers
    ).json()
    assert hits[0]["inWatchlist"] is True


def test_preferences_round_trip(client, headers):
    assert client.get("/api/preferences", headers=headers).json()["simpleMode"] is False
    updated = client.patch("/api/preferences", json={"simpleMode": True}, headers=headers).json()
    assert updated["simpleMode"] is True
    assert client.get("/api/preferences", headers=headers).json()["simpleMode"] is True


@pytest.mark.parametrize("path", ["/api/watchlists/9999/feed", "/api/watchlists/9999/timeline"])
def test_missing_watchlist_returns_a_consistent_error_shape(client, headers, path):
    body = client.get(path, headers=headers)
    assert body.status_code == 404
    assert set(body.json()["error"]) == {"code", "message", "details"}
