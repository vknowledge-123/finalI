"""Local teaching server: never deploy publicly; memory resets on restart."""
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ConfigDict

from domain import Position


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9 _-]+$")
    side: Literal["BUY", "SELL"] = "BUY"
    qty: int = Field(default=1, gt=0, le=100, strict=True)
    target_pct: Decimal = Field(default=Decimal("1"), gt=0, lt=100, allow_inf_nan=False)
    sl_pct: Decimal = Field(default=Decimal("0.5"), gt=0, lt=100, allow_inf_nan=False)


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=100)
    strategy: str
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9]{0,19}$")
    price: Decimal = Field(gt=0, allow_inf_nan=False)


class Tick(BaseModel):
    price: Decimal = Field(gt=0, allow_inf_nan=False)


def create_app():
    app = FastAPI(title="Paper Testing Lab")
    configs, positions, seen = {}, {}, {}

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

    @app.get("/health")
    async def health():
        return {"ok": True, "mode": "PAPER_ONLY"}

    @app.post("/api/strategies", status_code=201)
    async def save(config: Strategy):
        configs[config.name] = config
        return config

    @app.get("/api/strategies")
    async def list_configs():
        return list(configs.values())

    @app.delete("/api/strategies/{name}", status_code=204)
    async def delete(name: str):
        if name not in configs:
            raise HTTPException(404, "CONFIG_NOT_FOUND")
        del configs[name]

    @app.post("/api/signals")
    async def signal(data: Signal):
        payload = data.model_dump()
        if data.event_id in seen:
            old, result = seen[data.event_id]
            if old != payload:
                raise HTTPException(409, "IDEMPOTENCY_CONFLICT")
            return result
        if data.strategy not in configs:
            raise HTTPException(404, "CFG_MISSING")
        key = (data.strategy, data.symbol)
        if key in positions and positions[key].status == "OPEN":
            raise HTTPException(409, "ALREADY_OPEN")
        config = configs[data.strategy]
        position = Position.from_fill(config.name, data.symbol, config.side,
                                      config.qty, data.price, config.target_pct, config.sl_pct)
        positions[key] = position
        result = position.as_dict()
        seen[data.event_id] = (payload, result)
        return result

    @app.get("/api/positions")
    async def list_positions():
        return [p.as_dict() for p in positions.values()]

    @app.post("/api/ticks/{symbol}")
    async def tick(symbol: str, data: Tick):
        for position in positions.values():
            if position.symbol == symbol:
                position.mark(data.price)
        return {"ok": True}

    @app.post("/api/exits/{strategy}/{symbol}")
    async def exit_signal(strategy: str, symbol: str):
        position = positions.get((strategy, symbol))
        if position is None:
            raise HTTPException(404, "POSITION_NOT_FOUND")
        if position.status == "OPEN":
            position.status, position.exit_reason = "CLOSED", "ALERT_EXIT"
        return position.as_dict()

    return app


app = create_app()
