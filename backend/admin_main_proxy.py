"""Proxy vision-dependent main-app (8000) routes for the admin panel on 8080."""

from __future__ import annotations

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response

MAIN_API = "http://127.0.0.1:8000"
PROXY_HEADERS = {"X-Injection-Admin-Proxy": "loopback-proxy"}

# Reused connection pool — avoids opening a fresh socket on every proxied call.
_client = httpx.AsyncClient(
    base_url=MAIN_API,
    timeout=30.0,
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


async def proxy_main(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 30.0,
) -> Response:
    headers: dict[str, str] = dict(PROXY_HEADERS)
    if content_type:
        headers["Content-Type"] = content_type
    try:
        r = await _client.request(method, path, content=body, headers=headers or None, timeout=timeout)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ana uygulama (8000) erisilemiyor: {e}") from e

    skip = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    out_headers = {k: v for k, v in r.headers.items() if k.lower() not in skip}
    media = r.headers.get("content-type")
    if r.status_code >= 400 and media and "json" in media:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Ana uygulama hatasi")
    return Response(content=r.content, media_type=media, headers=out_headers)


async def proxy_main_from_request(request: Request, path: str, *, timeout: float = 30.0) -> Response:
    body = await request.body()
    ct = request.headers.get("content-type")
    return await proxy_main(request.method, path, body=body or None, content_type=ct, timeout=timeout)
