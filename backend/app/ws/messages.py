from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SnapshotMessage(BaseModel):
    type: str = "snapshot"
    data: dict[str, Any]
