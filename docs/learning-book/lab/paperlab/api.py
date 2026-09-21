"""Paper-only API. Bind to loopback; authentication is a later assignment."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Literal
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .domain import RiskState, advance, marked_pnl


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    side: Literal["BUY", "SELL"] = "BUY"
    quantity: int = Field(default=1, strict=True, gt=0, le=10000)
    target_pct: Decimal = Field(default=Decimal("2"), gt=0, lt=100, allow_inf_nan=False)
    stop_pct: Decimal = Field(default=Decimal("1"), gt=0, lt=100, allow_inf_nan=False)
    trailing_enabled: bool = False
    trailing_pct: Decimal = Field(default=Decimal("1"), gt=0, lt=100, allow_inf_nan=False)
    cost_enabled: bool = False
    cost_rr: Decimal = Field(default=Decimal("2"), gt=0, le=100, allow_inf_nan=False)
    retry_count: int = Field(default=0, strict=True, ge=0, le=3)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Strategy name is required")
        return value


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=100)
    strategy: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=30)
    price: Decimal = Field(gt=0, le=Decimal("10000000"), allow_inf_nan=False)

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value):
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9&.-]{1,30}", value):
            raise ValueError("Invalid paper symbol")
        return value


class Tick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    price: Decimal = Field(gt=0, le=Decimal("10000000"), allow_inf_nan=False)


def canonical(name):
    return " ".join(name.split()).casefold()


def create_app(database: str | Path) -> FastAPI:
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction():
        connection = sqlite3.connect(database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    with transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS configs (name TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS receipts (event_id TEXT PRIMARY KEY, digest TEXT NOT NULL, position_id TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY, strategy TEXT NOT NULL, symbol TEXT NOT NULL,
            status TEXT NOT NULL, payload TEXT NOT NULL)""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_open_position
            ON positions(strategy, symbol) WHERE status = 'OPEN'""")

    app = FastAPI(title="Trading School Paper Lab")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "detail": str(exc.detail)})

    @app.get("/health")
    def health():
        return {"ok": True, "mode": "PAPER_ONLY"}

    @app.get("/", response_class=HTMLResponse)
    def home():
        return Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")

    @app.post("/api/configs")
    def save_strategy(config: Strategy):
        data = config.model_dump(mode="json")
        with transaction() as db:
            db.execute("""INSERT INTO configs(name, payload) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET payload=excluded.payload""",
                (canonical(config.name), json.dumps(data)))
        return {"ok": True, "strategy": data}

    @app.get("/api/configs")
    def configs():
        with transaction() as db:
            rows = db.execute("SELECT payload FROM configs ORDER BY name").fetchall()
        return {"ok": True, "strategies": [json.loads(row[0]) for row in rows]}

    @app.post("/api/signals")
    def signal(payload: Signal):
        name = canonical(payload.strategy)
        normalized = payload.model_dump(mode="json")
        normalized["strategy"] = name
        normalized["price"] = str(payload.price.normalize())
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        with transaction() as db:
            previous = db.execute("SELECT * FROM receipts WHERE event_id=?", (payload.event_id,)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise HTTPException(409, "EVENT_ID_CONFLICT")
                return {"ok": True, "duplicate": True, "position_id": previous["position_id"]}
            row = db.execute("SELECT payload FROM configs WHERE name=?", (name,)).fetchone()
            if not row:
                raise HTTPException(404, "CONFIG_NOT_FOUND")
            config = Strategy.model_validate_json(row[0])
            existing = db.execute("SELECT id FROM positions WHERE strategy=? AND symbol=? AND status='OPEN'",
                                  (name, payload.symbol)).fetchone()
            if existing:
                raise HTTPException(409, "ALREADY_OPEN")
            sign = Decimal("1") if config.side == "BUY" else Decimal("-1")
            position_id = uuid.uuid4().hex
            # The local simulator fills immediately at the supplied synthetic price.
            # A real adapter must wait for independent broker fill evidence.
            position = {
                "id": position_id, "strategy": config.name, "symbol": payload.symbol,
                "side": config.side, "quantity": config.quantity,
                "entry": str(payload.price), "ltp": str(payload.price),
                "target": str(payload.price * (1 + sign * config.target_pct / 100)),
                "stop": str(payload.price * (1 - sign * config.stop_pct / 100)),
                "high_water": str(payload.price), "low_water": str(payload.price),
                "config": config.model_dump(mode="json"), "pnl": "0",
                "status": "OPEN", "exit_reason": None,
            }
            db.execute("INSERT INTO positions VALUES (?, ?, ?, ?, ?)",
                       (position_id, name, payload.symbol, "OPEN", json.dumps(position)))
            db.execute("INSERT INTO receipts VALUES (?, ?, ?)", (payload.event_id, digest, position_id))
        return {"ok": True, "duplicate": False, "position_id": position_id}

    @app.post("/api/ticks")
    def tick(payload: Tick):
        changed = []
        with transaction() as db:
            rows = db.execute("SELECT payload FROM positions WHERE symbol=? AND status='OPEN'",
                              (payload.symbol.strip().upper(),)).fetchall()
            for row in rows:
                position = json.loads(row[0])
                config = Strategy.model_validate(position["config"])
                state = RiskState(
                    position["side"], Decimal(position["entry"]), Decimal(position["target"]),
                    Decimal(position["stop"]), Decimal(position["high_water"]), Decimal(position["low_water"]),
                    config.stop_pct, config.trailing_enabled, config.trailing_pct,
                    config.cost_enabled, config.cost_rr,
                )
                updated, reason = advance(state, payload.price)
                position.update(
                    ltp=str(payload.price), stop=str(updated.stop),
                    high_water=str(updated.high_water), low_water=str(updated.low_water),
                    pnl=str(marked_pnl(state.side, state.entry, payload.price, position["quantity"])),
                )
                if reason:
                    position.update(status="CLOSED", exit_reason=reason, exit_price=str(payload.price))
                db.execute("UPDATE positions SET status=?, payload=? WHERE id=?",
                           (position["status"], json.dumps(position), position["id"]))
                changed.append(position)
        return {"ok": True, "positions": changed}

    @app.get("/api/positions")
    def positions():
        with transaction() as db:
            rows = db.execute("SELECT payload FROM positions ORDER BY rowid").fetchall()
        return {"ok": True, "positions": [json.loads(row[0]) for row in rows]}

    return app


def default_app():
    return create_app(os.environ.get("PAPERLAB_DB", "paperlab.sqlite3"))
