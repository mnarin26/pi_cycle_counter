from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite:///./data/injection.db"
    cors_origins: str = "*"
    data_dir: Path = Path("./data")
    logs_dir: Path = Path("./logs")
    static_dir: Path | None = Path("../frontend/dist")
    frame_skip: int = 2
    ws_broadcast_hz: float = 8.0
    vision_queue_max: int = 500
    abnormal_cycle_sigma: float = 4.0

    @property
    def cors_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
