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
    hysteresis: Mapped[float] = mapped_column(Float, default=0.06)
    no_movement_timeout_s: Mapped[float] = mapped_column(Float, default=120.0)
    learning_session: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    current_mold_id: Mapped[int | None] = mapped_column(ForeignKey("molds.id"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    qr_code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    camera: Mapped["Camera"] = relationship(back_populates="machines")


class Mold(Base):
    __tablename__ = "molds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    qr_code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate")  # candidate, active, ignored
    avg_cycle_s: Mapped[float] = mapped_column(Float, default=0.0)
    tolerance_s: Mapped[float] = mapped_column(Float, default=0.35)
    stdev_limit_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    machine_links: Mapped[list["MoldMachine"]] = relationship(back_populates="mold")


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


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
