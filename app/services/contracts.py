from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ChartinkSignalJob:
    user_id: int
    alert_name: str
    symbols: List[str]
    timestamp: str
    content_type: str = ""
    method: str = "POST"
    payload_keys: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceResult:
    ok: bool
    payload: Dict[str, Any]

