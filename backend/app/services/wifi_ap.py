"""Read/update Raspberry Pi Wi-Fi AP — hostapd (primary) or NetworkManager profile."""

from __future__ import annotations

import os
import shutil
import subprocess
from configparser import ConfigParser
from pathlib import Path

DEFAULT_CONNECTION_ID = os.environ.get("WIFI_AP_CONNECTION", "pi-wifi-ap")
HOSTAPD_CONF = Path(os.environ.get("HOSTAPD_CONF", "/etc/hostapd/hostapd.conf"))
NM_CONNECTIONS_DIR = Path("/etc/NetworkManager/system-connections")
WLAN_IFACE = os.environ.get("WIFI_AP_IFACE", "wlan0")


def _run(
    cmd: list[str],
    *,
    use_sudo: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if use_sudo:
        if not shutil.which("sudo"):
            raise RuntimeError("sudo bulunamadi — Wi-Fi AP ayari icin root gerekir")
        cmd = ["sudo", "-n", *cmd]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=45,
        input=input_text,
    )


def _validate_ssid(ssid: str) -> str:
    s = ssid.strip()
    if not s or len(s) > 32:
        raise ValueError("SSID 1–32 karakter olmali")
    if any(ord(c) < 32 for c in s):
        raise ValueError("SSID gecersiz karakter iceriyor")
    return s


def _validate_psk(psk: str) -> str:
    p = psk.strip()
    if len(p) < 8 or len(p) > 63:
        raise ValueError("Wi-Fi sifresi 8–63 karakter olmali (WPA2)")
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise ValueError("Wi-Fi sifresi gecersiz karakter iceriyor")
    return p


def _hostapd_service_active() -> bool:
    r = _run(["systemctl", "is-active", "hostapd"])
    return r.returncode == 0 and (r.stdout or "").strip() == "active"


def _iw_ap_active() -> bool:
    r = _run(["iw", "dev", WLAN_IFACE, "info"], use_sudo=True)
    if r.returncode != 0:
        r = _run(["iw", "dev", WLAN_IFACE, "info"])
    text = (r.stdout or "").lower()
    return "type ap" in text


def _detect_backend() -> str:
    if HOSTAPD_CONF.is_file() and (_hostapd_service_active() or _iw_ap_active()):
        return "hostapd"
    if shutil.which("nmcli"):
        show = _run(["nmcli", "connection", "show", DEFAULT_CONNECTION_ID])
        if show.returncode == 0:
            return "networkmanager"
    if HOSTAPD_CONF.is_file():
        return "hostapd"
    return "unknown"


def _read_hostapd_conf_text() -> str:
    r = _run(["cat", str(HOSTAPD_CONF)], use_sudo=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "hostapd.conf okunamadi").strip())
    return r.stdout


def _parse_hostapd_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _patch_hostapd_conf(text: str, *, ssid: str | None, passphrase: str | None) -> str:
    lines = text.splitlines()
    out: list[str] = []
    ssid_set = False
    pass_set = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ssid=") and ssid is not None:
            out.append(f"ssid={ssid}")
            ssid_set = True
        elif stripped.startswith("wpa_passphrase=") and passphrase is not None:
            out.append(f"wpa_passphrase={passphrase}")
            pass_set = True
        else:
            out.append(line)
    if ssid is not None and not ssid_set:
        out.append(f"ssid={ssid}")
    if passphrase is not None and not pass_set:
        out.append(f"wpa_passphrase={passphrase}")
    body = "\n".join(out)
    if not body.endswith("\n"):
        body += "\n"
    return body


def _write_hostapd_conf(content: str) -> None:
    r = _run(["tee", str(HOSTAPD_CONF)], use_sudo=True, input_text=content)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "hostapd.conf yazilamadi").strip())


def _restart_hostapd() -> None:
    r = _run(["systemctl", "restart", "hostapd"], use_sudo=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "hostapd yeniden baslatilamadi").strip())
    if not _hostapd_service_active():
        raise RuntimeError("hostapd restart sonrasi servis aktif degil")


def _hostapd_sudo_ready() -> bool:
    return Path("/etc/sudoers.d/injection-monitor-hostapd").is_file()


def _can_configure_hostapd() -> bool:
    if not HOSTAPD_CONF.is_file() or not _hostapd_sudo_ready():
        return False
    try:
        r = _run(["cat", str(HOSTAPD_CONF)], use_sudo=True)
        return r.returncode == 0
    except Exception:
        return False


def _can_sudo_nmcli() -> bool:
    if not shutil.which("nmcli"):
        return False
    try:
        r = _run(
            ["nmcli", "-g", "connection.id", "connection", "show", DEFAULT_CONNECTION_ID],
            use_sudo=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def _sync_nm_profile(ssid: str, password: str | None) -> None:
    if not _can_sudo_nmcli():
        return
    _run(
        ["nmcli", "connection", "modify", DEFAULT_CONNECTION_ID, "802-11-wireless.ssid", ssid],
        use_sudo=True,
    )
    if password:
        _run(
            ["nmcli", "connection", "modify", DEFAULT_CONNECTION_ID, "wifi-sec.psk", password],
            use_sudo=True,
        )


def _status_hostapd() -> dict:
    active = _hostapd_service_active() or _iw_ap_active()
    ssid = None
    has_password = False
    channel = None
    profile_exists = HOSTAPD_CONF.is_file()
    if profile_exists:
        try:
            kv = _parse_hostapd_kv(_read_hostapd_conf_text())
            ssid = kv.get("ssid") or None
            has_password = bool(kv.get("wpa_passphrase"))
            channel = kv.get("channel") or None
        except Exception:
            pass
    can = _can_configure_hostapd()
    hint = None
    if profile_exists and not can:
        hint = (
            "hostapd ayari icin pi kullanicisina passwordless sudo verin "
            "(tools/configure_wifi_ap_sudo.sh)."
        )
    return {
        "backend": "hostapd",
        "connection_id": str(HOSTAPD_CONF),
        "ssid": ssid,
        "has_password": has_password,
        "active": active,
        "mode": "ap" if active else None,
        "channel": channel,
        "profile_exists": profile_exists,
        "can_configure": can,
        "hint": hint,
    }


def _connection_file(connection_id: str) -> Path | None:
    direct = NM_CONNECTIONS_DIR / f"{connection_id}.nmconnection"
    if direct.is_file():
        return direct
    if not NM_CONNECTIONS_DIR.is_dir():
        return None
    for fp in NM_CONNECTIONS_DIR.glob("*.nmconnection"):
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if f"id={connection_id}" in text:
            return fp
    return None


def _status_networkmanager(connection_id: str) -> dict:
    info: dict = {
        "backend": "networkmanager",
        "connection_id": connection_id,
        "ssid": None,
        "has_password": False,
        "active": False,
        "mode": None,
        "channel": None,
        "profile_exists": False,
        "can_configure": False,
        "hint": None,
    }
    show = _run(["nmcli", "connection", "show", connection_id])
    if show.returncode != 0:
        info["hint"] = f"'{connection_id}' profili bulunamadi."
        return info
    info["profile_exists"] = True

    def _field(prop: str) -> str:
        r = _run(["nmcli", "-g", prop, "connection", "show", connection_id])
        return (r.stdout or "").strip() if r.returncode == 0 else ""

    info["ssid"] = _field("802-11-wireless.ssid") or None
    info["mode"] = _field("802-11-wireless.mode") or None
    info["channel"] = _field("802-11-wireless.channel") or None
    state = _field("GENERAL.STATE").lower()
    info["active"] = "activated" in state or _iw_ap_active()

    fp = _connection_file(connection_id)
    if fp:
        try:
            r = _run(["cat", str(fp)], use_sudo=True)
            if r.returncode == 0:
                parser = ConfigParser()
                parser.read_string(r.stdout)
                if parser.has_option("wifi-security", "psk"):
                    info["has_password"] = bool(parser.get("wifi-security", "psk", fallback="").strip())
        except Exception:
            pass

    info["can_configure"] = _can_sudo_nmcli()
    if info["profile_exists"] and not info["can_configure"]:
        info["hint"] = "AP ayari icin passwordless sudo nmcli gerekir."
    return info


def get_wifi_ap_status(connection_id: str = DEFAULT_CONNECTION_ID) -> dict:
    backend = _detect_backend()
    if backend == "hostapd":
        st = _status_hostapd()
    elif backend == "networkmanager":
        st = _status_networkmanager(connection_id)
    else:
        st = {
            "backend": "unknown",
            "connection_id": connection_id,
            "ssid": None,
            "has_password": False,
            "active": _iw_ap_active(),
            "mode": "ap" if _iw_ap_active() else None,
            "channel": None,
            "profile_exists": False,
            "can_configure": False,
            "hint": "hostapd veya NetworkManager AP profili bulunamadi.",
        }
    st["nmcli_available"] = shutil.which("nmcli") is not None
    return st


def _apply_hostapd(ssid: str, password: str | None) -> bool:
    text = _read_hostapd_conf_text()
    updated = _patch_hostapd_conf(text, ssid=ssid, passphrase=password)
    _write_hostapd_conf(updated)
    _sync_nm_profile(ssid, password)
    _restart_hostapd()
    return True


def _apply_networkmanager(
    ssid: str,
    password: str | None,
    *,
    connection_id: str,
    reconnect: bool,
) -> bool:
    status_before = _status_networkmanager(connection_id)
    if not status_before["profile_exists"]:
        raise RuntimeError(f"Baglanti profili bulunamadi: {connection_id}")
    was_active = status_before["active"] or _iw_ap_active()

    r1 = _run(
        ["nmcli", "connection", "modify", connection_id, "802-11-wireless.ssid", ssid],
        use_sudo=True,
    )
    if r1.returncode != 0:
        raise RuntimeError((r1.stderr or r1.stdout or "SSID guncellenemedi").strip())

    if password:
        r2 = _run(
            ["nmcli", "connection", "modify", connection_id, "wifi-sec.psk", password],
            use_sudo=True,
        )
        if r2.returncode != 0:
            raise RuntimeError((r2.stderr or r2.stdout or "Sifre guncellenemedi").strip())

    reconnected = False
    if reconnect and was_active:
        _run(["nmcli", "connection", "down", connection_id], use_sudo=True)
        r_up = _run(["nmcli", "connection", "up", connection_id], use_sudo=True)
        if r_up.returncode != 0:
            raise RuntimeError((r_up.stderr or r_up.stdout or "AP yeniden baslatilamadi").strip())
        reconnected = True
    return reconnected


def set_wifi_ap(
    ssid: str,
    password: str | None = None,
    *,
    connection_id: str = DEFAULT_CONNECTION_ID,
    reconnect: bool = True,
) -> dict:
    ssid_v = _validate_ssid(ssid)
    pass_v = _validate_psk(password) if password and password.strip() else None

    backend = _detect_backend()
    reconnected = False

    if backend == "hostapd" or HOSTAPD_CONF.is_file():
        if not _can_configure_hostapd():
            raise RuntimeError("hostapd icin sudo yetkisi yok")
        _apply_hostapd(ssid_v, pass_v)
        reconnected = True
    elif backend == "networkmanager":
        if not _can_sudo_nmcli():
            raise RuntimeError("nmcli icin sudo yetkisi yok")
        reconnected = _apply_networkmanager(
            ssid_v, pass_v, connection_id=connection_id, reconnect=reconnect
        )
    else:
        raise RuntimeError("Wi-Fi AP yapilandirmasi bulunamadi")

    out = get_wifi_ap_status(connection_id)
    out["reconnected"] = reconnected
    return out
