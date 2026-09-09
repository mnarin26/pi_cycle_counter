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
    # After opposite wait visit, tolerate longer signal loss (reflector under panel).
    cycle_unknown_grace_after_extreme_s: float = 12.0
    cycle_min_travel_range: float = 0.18
    # How close to axis end counts as "at endpoint" for cycle travel checks.
    cycle_endpoint_margin: float = 0.15
    # Fallback swing amplitude when a machine has no per-machine value. Real
    # setting lives per machine (machines.min_prominence, default 0.1). Idle
    # jitter tops out ~0.04; real strokes >=0.30 (validated on AF-1/4/5).
    cycle_min_prominence: float = 0.1
    # Legacy continuous diag list (empty = off). Prefer per-machine diag_until.
    diag_machine_ids: str = ""
    # Legacy JPEG/clip settings (ignored; CSV-only diag since 2026-09).
    diag_ring_seconds: float = 55.0
    diag_jpeg_max_width: int = 480
    diag_jpeg_quality: int = 52
    diag_jpeg_hz: float = 0.0
    diag_long_ratio: float = 1.7
    diag_long_min_s: float = 24.0
    diag_max_clips_per_day: int = 0
    diag_clip_every_nth_after: int = 8
    diag_stale_frame_ms: float = 2000.0
    diag_retention_days: int = 14

    # Anomaly window: SHORT/LONG ±N cycles → logs/anomaly/.../pos.csv
    anomaly_enabled: bool = False
    anomaly_short_s: float = 10.0
    anomaly_long_s: float = 40.0
    anomaly_pre_cycles: int = 5
    anomaly_post_cycles: int = 5
    anomaly_max_cycles: int = 40
    anomaly_max_window_s: float = 900.0
    anomaly_ring_ticks: int = 6000
    # Min interval between anomaly pos samples (vision cost control).
    anomaly_tick_interval_ms: float = 100.0
    # Empty = all machines; comma list e.g. "3,8"
    anomaly_machine_ids: str = ""
    anomaly_retention_days: int = 14

    # Per-machine daily cycle CSV under logs/machine_N/YYYY-MM-DD.csv (disk I/O each cycle).
    cycle_daily_csv_enabled: bool = False

    # Auth: embedded super password (overridable via .env SUPER_PASSWORD)
    super_password: str = "cb5BAC508"
    session_max_hours: float = 12.0
    session_idle_hours: float = 2.0
    session_cookie_name: str = "im_session"

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
