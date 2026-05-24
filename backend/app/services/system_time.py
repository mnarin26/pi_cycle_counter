"""Read / set Linux system clock (Raspberry Pi) via timedatectl."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone

COMMON_TIMEZONES = [
    "Europe/Istanbul",
    "Europe/London",
    "Europe/Berlin",
    "UTC",
    "Asia/Dubai",
]


def _run(cmd: list[str], *, use_sudo: bool = False) -> subprocess.CompletedProcess[str]:
    if use_sudo:
        if not shutil.which("sudo"):
            raise RuntimeError("sudo bulunamadi — saat ayari icin root gerekir")
        cmd = ["sudo", "-n", *cmd]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20)


def _parse_timedatectl_show(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _can_sudo_timedatectl() -> bool:
    if not shutil.which("timedatectl") or not shutil.which("sudo"):
        return False
    try:
        r = _run(["timedatectl", "show", "-p", "Timezone"], use_sudo=True)
        return r.returncode == 0
    except Exception:
        return False


def get_time_status() -> dict:
    now_utc = datetime.now(timezone.utc)
    local_now = datetime.now().astimezone()
    info: dict = {
        "platform": platform.system(),
        "utc_now": now_utc.isoformat().replace("+00:00", "Z"),
        "local_now": local_now.isoformat(),
        "timezone": str(local_now.tzinfo or "local"),
        "ntp_synchronized": None,
        "ntp_active": None,
        "timedatectl_available": shutil.which("timedatectl") is not None,
        "can_set_time": False,
        "common_timezones": COMMON_TIMEZONES,
        "hint": None,
    }

    if info["timedatectl_available"]:
        r = _run(["timedatectl", "show"], use_sudo=False)
        if r.returncode == 0:
            fields = _parse_timedatectl_show(r.stdout)
            info["timezone"] = fields.get("Timezone", info["timezone"])
            if "NTPSynchronized" in fields:
                info["ntp_synchronized"] = fields["NTPSynchronized"] == "yes"
            if "NTP" in fields:
                info["ntp_active"] = fields["NTP"] == "yes"
        info["can_set_time"] = _can_sudo_timedatectl()
        if not info["can_set_time"]:
            info["hint"] = (
                "Saat degistirmek icin pi kullanicisina passwordless sudo timedatectl verin "
                "(tools/configure_timedatectl_sudo.sh)."
            )
    else:
        info["hint"] = "timedatectl yok (Windows gelistirme ortami); Pi uzerinde calisir."

    return info


def set_timezone(tz: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_+-]+/[A-Za-z0-9_+-]+", tz) and tz != "UTC":
        raise ValueError("Gecersiz timezone")
    r = _run(["timedatectl", "set-timezone", tz], use_sudo=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "timezone ayarlanamadi").strip())


def set_manual_time(datetime_local: str) -> None:
    """Set wall-clock time; disables NTP until re-enabled."""
    raw = datetime_local.strip().replace(" ", "T")
    if len(raw) == 16:
        raw += ":00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise ValueError("datetime_local gecersiz (YYYY-MM-DDTHH:MM)") from e

    wall = dt.strftime("%Y-%m-%d %H:%M:%S")
    r_ntp = _run(["timedatectl", "set-ntp", "false"], use_sudo=True)
    if r_ntp.returncode != 0:
        raise RuntimeError((r_ntp.stderr or "NTP kapatilamadi").strip())

    r = _run(["timedatectl", "set-time", wall], use_sudo=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "saat ayarlanamadi").strip())


def set_ntp_enabled(enabled: bool) -> None:
    val = "true" if enabled else "false"
    r = _run(["timedatectl", "set-ntp", val], use_sudo=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "NTP ayarlanamadi").strip())
