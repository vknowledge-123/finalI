import pytest

pytestmark = pytest.mark.api


def seed(client, name="demo"):
    response = client.post("/api/strategies", json={"name": name, "qty": 2})
    assert response.status_code == 201


def payload(event="one", strategy="demo"):
    return {"event_id": event, "strategy": strategy, "symbol": "TEST", "price": "100"}


def test_config_lifecycle(client):
    seed(client)
    assert client.get("/api/strategies").json()[0]["qty"] == 2
    assert client.delete("/api/strategies/demo").status_code == 204
    assert client.get("/api/strategies").json() == []


def test_config_missing_has_no_position(client):
    response = client.post("/api/signals", json=payload())
    assert response.status_code == 404
    assert response.json()["detail"] == "CFG_MISSING"
    assert client.get("/api/positions").json() == []


@pytest.mark.parametrize("qty", [0, -1, True, "2", 101])
def test_bad_qty_has_no_write(client, qty):
    assert client.post("/api/strategies", json={"name": "bad", "qty": qty}).status_code == 422
    assert client.get("/api/strategies").json() == []


def test_duplicate_and_conflicting_signal(client):
    seed(client)
    first = client.post("/api/signals", json=payload())
    assert first.status_code == 200
    assert client.post("/api/signals", json=payload()).json() == first.json()
    assert len(client.get("/api/positions").json()) == 1
    changed = dict(payload(), price="101")
    assert client.post("/api/signals", json=changed).status_code == 409


def test_exit_cannot_close_other_strategy(client):
    for name in ("alpha", "beta"):
        seed(client, name)
        assert client.post("/api/signals", json=payload(name, name)).status_code == 200
    client.post("/api/exits/alpha/TEST")
    states = {p["strategy"]: p["status"] for p in client.get("/api/positions").json()}
    assert states == {"alpha": "CLOSED", "beta": "OPEN"}


def test_tick_target_and_closed_pnl(client):
    seed(client)
    client.post("/api/signals", json=payload())
    assert client.post("/api/ticks/TEST", json={"price": "101"}).status_code == 200
    position = client.get("/api/positions").json()[0]
    assert position["exit_reason"] == "TARGET"
    assert position["pnl"] == "2"
