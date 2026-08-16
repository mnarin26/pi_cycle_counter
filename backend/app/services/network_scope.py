"""Classify the HTTP client as company-local vs Tailscale/remote.

Tailscale uses CGNAT 100.64.0.0/10 (and fd7a:115c:a1e0::/48). Python treats
that range as private, so Tailscale MUST be checked before is_private.
X-Forwarded-For is ignored here so a remote client cannot spoof a LAN IP.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

# Tailscale CGNAT + default IPv6 ULA prefix.
_TAILSCALE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


def direct_client_ip(request: Request) -> str | None:
    if not request.client:
        return None
    return (request.client.host or "").strip() or None


def _parse_ip(ip: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    raw = (ip or "").strip()
    if not raw:
        return None
    if raw.startswith("::ffff:"):
        raw = raw[7:]
    if "%" in raw:
        raw = raw.split("%", 1)[0]
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


def is_tailscale_ip(ip: str | None) -> bool:
    addr = _parse_ip(ip)
    if addr is None:
        return False
    return any(addr in net for net in _TAILSCALE_NETS)


def is_company_local_ip(ip: str | None) -> bool:
    """Ethernet / factory AP / loopback — not Tailscale."""
    if is_tailscale_ip(ip):
        return False
    addr = _parse_ip(ip)
    if addr is None:
        return False
    return bool(addr.is_loopback or addr.is_private or addr.is_link_local)


def login_mode_for_ip(ip: str | None) -> str:
    return "local" if is_company_local_ip(ip) else "remote"
