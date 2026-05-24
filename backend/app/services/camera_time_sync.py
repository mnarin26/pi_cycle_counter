"""Sync IP camera wall clock (OSD) from Pi system time via vendor HTTP APIs."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo


def parse_rtsp_url(rtsp_url: str) -> tuple[str | None, str, str]:
    p = urlparse((rtsp_url or "").strip())
    host = p.hostname
    user = p.username or ""
    password = p.password or ""
    return host, user, password


def _tz_to_gmt_offset(tz_name: str) -> str:
    try:
        z = ZoneInfo(tz_name)
        now = datetime.now(z)
        off = now.utcoffset()
        if off is None:
            return "GMT+00:00"
        secs = int(off.total_seconds())
        sign = "+" if secs >= 0 else "-"
        secs = abs(secs)
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return f"GMT{sign}{h:02d}:{m:02d}"
    except Exception:
        return "GMT+03:00"


def _request(
    url: str,
    user: str,
    password: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> tuple[int, str]:
    hdrs = dict(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    if user or password:
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, url, user, password)
        digest = urllib.request.HTTPDigestAuthHandler(password_mgr)
        basic = urllib.request.HTTPBasicAuthHandler(password_mgr)
        opener = urllib.request.build_opener(digest, basic)
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body


def _try_hikvision(host: str, user: str, password: str, local_dt: datetime, tz_name: str) -> bool:
    wall = local_dt.strftime("%Y-%m-%dT%H:%M:%S")
    gmt = _tz_to_gmt_offset(tz_name)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Time version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
  <timeMode>manual</timeMode>
  <localTime>{wall}</localTime>
  <timeZone>{gmt}</timeZone>
</Time>"""
    url = f"http://{host}/ISAPI/System/time"
    code, body = _request(
        url,
        user,
        password,
        method="PUT",
        data=xml.encode("utf-8"),
        headers={"Content-Type": "application/xml"},
    )
    if 200 <= code < 300:
        return True
    if code == 401:
        return False
    return "OK" in body.upper() or "success" in body.lower()


def _try_dahua(host: str, user: str, password: str, local_dt: datetime, _tz_name: str) -> bool:
    wall = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    q = (
        "action=setConfig"
        "&NTP.Enable=false"
        f"&Time.LocalTime={quote(wall)}"
        "&Time.TimeFormat=0"
    )
    url = f"http://{host}/cgi-bin/configManager.cgi?{q}"
    code, body = _request(url, user, password, method="GET")
    if 200 <= code < 300 and "OK" in body.upper():
        return True
    url2 = f"http://{host}/cgi-bin/global.cgi?action=setCurrentTime&time={quote(wall)}"
    code2, body2 = _request(url2, user, password, method="GET")
    return 200 <= code2 < 300 and ("OK" in body2.upper() or "success" in body2.lower())


def _try_xmeye(host: str, user: str, password: str, local_dt: datetime, _tz_name: str) -> bool:
    """Common on XM / generic DVR firmware."""
    wall = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    url = f"http://{host}/cgi-bin/hi3510/param.cgi?cmd=setservertime&{quote(wall)}"
    code, body = _request(url, user, password, method="GET")
    return 200 <= code < 300 and ("ok" in body.lower() or code == 200)


def sync_camera_time(
    rtsp_url: str,
    *,
    timezone: str = "Europe/Istanbul",
    when: datetime | None = None,
) -> dict:
    host, user, password = parse_rtsp_url(rtsp_url)
    if not host:
        raise ValueError("RTSP adresinden kamera IP okunamadi")

    if not user:
        raise ValueError("RTSP URL icinde kullanici adi yok (rtsp://user:pass@ip/...)")

    try:
        local_dt = when or datetime.now(ZoneInfo(timezone))
    except Exception:
        local_dt = when or datetime.now()

    attempts: list[tuple[str, Callable[..., bool]]] = [
        ("hikvision_isapi", lambda: _try_hikvision(host, user, password, local_dt, timezone)),
        ("dahua_cgi", lambda: _try_dahua(host, user, password, local_dt, timezone)),
        ("xmeye_cgi", lambda: _try_xmeye(host, user, password, local_dt, timezone)),
    ]
    errors: list[str] = []
    for name, fn in attempts:
        try:
            if fn():
                return {
                    "ok": True,
                    "host": host,
                    "method": name,
                    "applied_local_time": local_dt.isoformat(),
                    "timezone": timezone,
                }
        except Exception as e:
            errors.append(f"{name}: {e}")

    raise RuntimeError(
        "Kamera saati ayarlanamadi (Hikvision/Dahua/XM denenmedi veya yetki yok). "
        "Kameranin web arayuzunden NTP/saat ayarini kontrol edin. "
        + ("; ".join(errors[:3]) if errors else "")
    )
