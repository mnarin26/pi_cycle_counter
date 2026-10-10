from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rtsp_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    target_width: Mapped[int] = mapped_column(Integer, default=640)
    target_fps: Mapped[int] = mapped_column(Integer, default=8)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_frame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="disconnected")  # ok, disconnected, error

    machines: Mapped[list["Machine"]] = relationship(back_populates="camera")


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slot_index: Mapped[int] = mapped_column(Integer, default=1)
    roi_polygon: Mapped[str] = mapped_column(Text, default="[]")  # JSON list [[x,y],...] normalized 0-1
    axis_p0: Mapped[str] = mapped_column(Text, default="[0,0.5]")  # JSON [x,y] normalized closed end
    axis_p1: Mapped[str] = mapped_column(Text, default="[1,0.5]")  # JSON open end
    threshold_mode: Mapped[str] = mapped_column(String(16), default="fixed")  # fixed, learned
    threshold_min: Mapped[int] = mapped_column(Integer, default=200)
    threshold_max: Mapped[int] = mapped_column(Integer, default=255)
    threshold_offset: Mapped[int] = mapped_column(Integer, default=0)  # manual +/- shift applied after base threshold
    line_thickness: Mapped[int] = mapped_column(Integer, default=7)  # perpendicular sampling window for 1D line probe
    reflector_len_min: Mapped[int | None] = mapped_column(Integer, nullable=True)  # learned lower bound in line samples
    reflector_len_max: Mapped[int | None] = mapped_column(Integer, nullable=True)  # learned upper bound in line samples
    occlusion_grace_ms: Mapped[int] = mapped_column(Integer, default=300)
    debounce_ms: Mapped[int] = mapped_column(Integer, default=80)
    stability_confirm_ms: Mapped[int] = mapped_column(Integer, default=500)
    open_position_1d: Mapped[float] = mapped_column(Float, default=0.85)
    closed_position_1d: Mapped[float] = mapped_column(Float, default=0.15)
    hysteresis: Mapped[float] = mapped_column(Float, default=0.06)  # unused by motion path; kept for SQLite compat
    min_change: Mapped[float] = mapped_column(Float, default=0.008)  # legacy; unused by counting
    # Stroke-detection swing amplitude (0..1 along the line). A peak/trough only
    # counts once the signal retraces at least this much. Above idle/park jitter,
    # below the shortest real stroke. Per machine; default 0.1.
    min_prominence: Mapped[float] = mapped_column(Float, default=0.1)
    # ISO datetime (UTC preferred). Process log window: diag_from <= now < diag_until.
    diag_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    diag_until: Mapped[str | None] = mapped_column(Text, nullable=True)
    no_movement_timeout_s: Mapped[float] = mapped_column(Float, default=120.0)
    # Chart: no cycle for this many minutes → zigzag gap + red idle ticks (default 3).
    idle_gap_minutes: Mapped[int] = mapped_column(Integer, default=3)
    # --- Schmitt-trigger counting (AF-4/5/6); others stay peak_trough ---
    counting_mode: Mapped[str] = mapped_column(String(16), default="peak_trough")  # peak_trough | schmitt
    closed_polarity: Mapped[str] = mapped_column(String(8), default="low")  # low = closed at low pos; high = closed at high pos
    closed_ref: Mapped[float | None] = mapped_column(Float, nullable=True)  # learned closed level (pos 0..1)
    closed_hyst: Mapped[float] = mapped_column(Float, default=0.05)
    smooth_win: Mapped[int] = mapped_column(Integer, default=5)
    learn_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    learned_at: Mapped[str | None] = mapped_column(Text, nullable=True)  # ISO datetime of last closed_ref learn
    learning_session: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    current_mold_id: Mapped[int | None] = mapped_column(ForeignKey("molds.id"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    qr_code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    camera: Mapped["Camera"] = relationship(back_populates="machines")


class Product(Base):
    """SKU / product name as it appears on order & stock Excel sheets."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    # Planner keeps this many pieces in stock (only the excess serves orders); NULL = no floor.
    min_stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    molds: Mapped[list["Mold"]] = relationship(back_populates="product")


class Mold(Base):
    __tablename__ = "molds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    qr_code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate")  # candidate, active, ignored
    avg_cycle_s: Mapped[float] = mapped_column(Float, default=0.0)
    target_cycle_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_target_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    work_mode: Mapped[str] = mapped_column(String(16), default="auto")  # auto | manual
    mount_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    removal_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tolerance_s: Mapped[float] = mapped_column(Float, default=0.35)
    stdev_limit_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    cavity_count: Mapped[int] = mapped_column(Integer, default=1)  # defined cavities
    # Operator-adjustable while mounted; None → use cavity_count
    active_cavity_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product: Mapped["Product | None"] = relationship(back_populates="molds")
    machine_links: Mapped[list["MoldMachine"]] = relationship(back_populates="mold")


class MoldMachineCapability(Base):
    """Which machines a mold may run on + manual priority (rule, not history)."""

    __tablename__ = "mold_machine_capabilities"
    __table_args__ = (UniqueConstraint("mold_id", "machine_id", name="uq_mold_cap_mold_machine"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mold_id: Mapped[int] = mapped_column(ForeignKey("molds.id"), nullable=False)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower = preferred


class MachineFault(Base):
    """Open-ended machine fault; end_at NULL until resolved. Planning skips open faults."""

    __tablename__ = "machine_faults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MoldRun(Base):
    """One continuous mold-on-machine work segment (not always closed at mold change)."""

    __tablename__ = "mold_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mold_id: Mapped[int] = mapped_column(ForeignKey("molds.id"), nullable=False)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # mold_change | long_stop
    camera_cycles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entered_press_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_cavity_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_added: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockMovement(Base):
    """Stock ledger. excel_reset qty is absolute stock; production/shipment are deltas."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # production | shipment | excel_reset
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # mold_run | order_line | import
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderImport(Base):
    __tablename__ = "order_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("order_imports.id"), nullable=True)
    product_name_raw: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | shipped | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlanRun(Base):
    """One planning suggestion batch (never auto-assigns)."""

    __tablename__ = "plan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # active | stale | superseded | backtest (backtest never becomes the current plan)
    status: Mapped[str] = mapped_column(String(16), default="active")
    stale_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    horizon_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prefs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlanSlot(Base):
    __tablename__ = "plan_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_run_id: Mapped[int] = mapped_column(ForeignKey("plan_runs.id"), nullable=False)
    order_line_id: Mapped[int | None] = mapped_column(ForeignKey("order_lines.id"), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    mold_id: Mapped[int | None] = mapped_column(ForeignKey("molds.id"), nullable=True)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    suggested_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suggested_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qty_planned: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Backtest lines come from a temporary Excel, not order_lines → keyed by line_key.
    line_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MoldMachine(Base):
    __tablename__ = "mold_machines"
    __table_args__ = (UniqueConstraint("mold_id", "machine_id", name="uq_mold_machines_mold_machine"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mold_id: Mapped[int] = mapped_column(ForeignKey("molds.id"), nullable=False)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cycles_attributed: Mapped[int] = mapped_column(Integer, default=0)
    last_avg_local: Mapped[float | None] = mapped_column(Float, nullable=True)

    mold: Mapped["Mold"] = relationship(back_populates="machine_links")


class Cycle(Base):
    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    mold_id: Mapped[int | None] = mapped_column(ForeignKey("molds.id"), nullable=True)
    cycle_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    t_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    t_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    mold_name_snapshot: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_state_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_counted: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"), nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MachineDowntime(Base):
    """Operator-entered downtime; end_at NULL = still down until closed."""

    __tablename__ = "machine_downtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # super, operator
    telegram_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyPassword(Base):
    __tablename__ = "daily_passwords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    valid_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD (Europe/Istanbul)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # super, operator, system
    telegram_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
