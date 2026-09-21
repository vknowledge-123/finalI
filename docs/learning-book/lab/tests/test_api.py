from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from paperlab.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite3")) as value:
        yield value


def save(client, **extra):
    return client.post("/api/configs", json={"name": "Practice", **extra})


def enter(client, **extra):
    return client.post("/api/signals", json={
        "event_id": "event-1", "strategy": "Practice", "symbol": "DEMO", "price": "100", **extra,
    })


def test_zero_survives_save_and_reload(client):
    assert save(client, retry_count=0).status_code == 200
    assert client.get("/api/configs").json()["strategies"][0]["retry_count"] == 0


@pytest.mark.parametrize("extra", [{"quantity": True}, {"quantity": 0}, {"side": "INVALID"},
                                  {"stop_pct": "NaN"}, {"misspelled": 2}, {"name": "  "}])
def test_invalid_configuration(client, extra):
    assert save(client, **extra).status_code == 422


def test_unknown_config(client):
    response = enter(client)
    assert response.status_code == 404
    assert response.json() == {"ok": False, "detail": "CONFIG_NOT_FOUND"}


def test_signal_duplicate_and_conflict(client):
    save(client)
    first = enter(client).json()
    second = enter(client, price="100.00").json()
    assert first["position_id"] == second["position_id"]
    assert second["duplicate"] is True
    assert enter(client, price="101").status_code == 409
    assert len(client.get("/api/positions").json()["positions"]) == 1


def test_different_event_same_open_position(client):
    save(client)
    enter(client)
    assert enter(client, event_id="event-2").status_code == 409


def test_simultaneous_duplicate_signal(client):
    save(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: enter(client), range(2)))
    assert all(item.status_code == 200 for item in responses)
    assert sorted(item.json()["duplicate"] for item in responses) == [False, True]
    assert len(client.get("/api/positions").json()["positions"]) == 1


def test_target_closes_and_cannot_reopen_from_duplicate(client):
    save(client, quantity=3)
    enter(client)
    response = client.post("/api/ticks", json={"symbol": "DEMO", "price": "103"})
    closed = response.json()["positions"][0]
    assert (closed["status"], closed["exit_reason"], closed["pnl"]) == ("CLOSED", "TARGET", "9")
    assert enter(client).json()["duplicate"] is True
    assert client.get("/api/positions").json()["positions"][0]["status"] == "CLOSED"


def test_short_target(client):
    save(client, side="SELL")
    enter(client)
    position = client.post("/api/ticks", json={"symbol": "DEMO", "price": "98"}).json()["positions"][0]
    assert (position["status"], position["pnl"]) == ("CLOSED", "2")


def test_config_edit_does_not_rewrite_open_position(client):
    save(client)
    enter(client)
    save(client, target_pct="20")
    position = client.post("/api/ticks", json={"symbol": "DEMO", "price": "103"}).json()["positions"][0]
    assert position["exit_reason"] == "TARGET"
    assert position["config"]["target_pct"] == "2"


def test_persistence_after_recreating_app(tmp_path):
    database = tmp_path / "restart.sqlite3"
    with TestClient(create_app(database)) as client:
        save(client)
        enter(client)
    with TestClient(create_app(database)) as client:
        assert len(client.get("/api/configs").json()["strategies"]) == 1
        assert client.get("/api/positions").json()["positions"][0]["status"] == "OPEN"


def test_strategy_scoped_open_guard(client):
    save(client)
    save(client, name="Other")
    assert enter(client).status_code == 200
    assert enter(client, event_id="event-2", strategy="Other").status_code == 200


def test_markup_symbol_rejected(client):
    save(client)
    assert enter(client, symbol="<script>").status_code == 422


def test_health_is_paper_only(client):
    assert client.get("/health").json() == {"ok": True, "mode": "PAPER_ONLY"}
