from __future__ import annotations

from typing import Any, Dict

from fastapi import Request

from .signal_intake import SignalIntakeService


class ApiGatewayService:
    """Thin HTTP-facing service boundary.

    FastAPI still owns routing, but this service keeps route handlers as
    delegation points instead of letting them place orders or mutate positions.
    """

    def __init__(self, signal_intake: SignalIntakeService) -> None:
        self.signal_intake = signal_intake

    async def receive_chartink_webhook(self, request: Request, user_id: int) -> Dict[str, Any]:
        return await self.signal_intake.submit_request(request, int(user_id))

    def status(self) -> Dict[str, Any]:
        return {"enabled": True}

