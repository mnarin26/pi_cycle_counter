from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute paths — cwd'den bagimsiz (admin/main ayni DB'yi kullanir).
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "*"
    data_dir: Path = BACKEND_DIR / "data"
    logs_dir: Path = BACKEND_DIR / "logs"
    static_dir: Path | None = BACKEND_DIR.parent / "frontend" / "dist"
    frame_skip: int = 2
    ws_broadcast_hz: float = 8.0
    vision_queue_max: int = 500
    abnormal_cycle_sigma: float = 4.0
    # When False, cycles are counted without auto mold match / post-stop / abnormal filter.
    auto_mold_matching: bool = False
    # In-progress cycle survives UNKNOWN up to this long (also scaled per machine).
    cycle_unknown_grace_s: float = 3.0
    # After axis endpoint visit, tolerate longer signal loss (reflector under panel).
    cycle_unknown_grace_after_extreme_s: float = 12.0
    cycle_endpoint_margin: float = 0.15
    cycle_min_travel_range: float = 0.18

    @property
    def database_url(self) -> str:
        db_path = (self.data_dir / "injection.db").resolve()
        return f"sqlite:///{db_path.as_posix()}"

    @property
    def cors_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
