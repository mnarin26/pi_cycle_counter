"""Parse and decode QR payloads for machines and molds."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np


class QrKind(str, Enum):
    MACHINE = "machine"
    MOLD = "mold"
    UNKNOWN = "unknown"


@dataclass
class QrPayload:
    kind: QrKind
    code: str
    raw: str


_MACHINE_PREFIXES = ("MACHINE:", "MAKINE:", "M:")
_MOLD_PREFIXES = ("MOLD:", "KALIP:", "K:")


def parse_qr_text(raw: str) -> QrPayload:
    text = (raw or "").strip()
    if not text:
        raise ValueError("QR bos")
    upper = text.upper()
    for prefix in _MACHINE_PREFIXES:
        if upper.startswith(prefix):
            code = text[len(prefix) :].strip()
            if not code:
                raise ValueError("Makine kodu bos")
            return QrPayload(kind=QrKind.MACHINE, code=code, raw=text)
    for prefix in _MOLD_PREFIXES:
        if upper.startswith(prefix):
            code = text[len(prefix) :].strip()
            if not code:
                raise ValueError("Kalip kodu bos")
            return QrPayload(kind=QrKind.MOLD, code=code, raw=text)
    # Düz metin: sayısal ise makine ID tahmini, aksi halde kalıp kodu
    if text.isdigit() and len(text) <= 4:
        return QrPayload(kind=QrKind.MACHINE, code=text, raw=text)
    return QrPayload(kind=QrKind.MOLD, code=text, raw=text)


def _qr_multi_decode(detector: cv2.QRCodeDetector, source) -> str | None:
    out = detector.detectAndDecodeMulti(source)
    if len(out) == 4:
        _ok, texts, points, _straight = out
    elif len(out) == 3:
        texts, points, _ = out
    else:
        return None
    if points is None or not texts:
        return None
    items = texts if isinstance(texts, (list, tuple)) else [texts]
    for t in items:
        if t and str(t).strip():
            return str(t).strip()
    return None


def _qr_single_decode(detector: cv2.QRCodeDetector, source) -> str | None:
    out = detector.detectAndDecode(source)
    if len(out) == 4:
        text = out[1]
    elif len(out) >= 1:
        text = out[0]
    else:
        return None
    if text and str(text).strip():
        return str(text).strip()
    return None


def decode_qr_from_image_bytes(data: bytes) -> str:
    if not data:
        raise ValueError("Bos goruntu")
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Goruntu okunamadi")
    detector = cv2.QRCodeDetector()

    def try_decode(source) -> str | None:
        hit = _qr_multi_decode(detector, source)
        if hit:
            return hit
        return _qr_single_decode(detector, source)

    hit = try_decode(img)
    if hit:
        return hit
    # Telegram sıkıştırması küçük QR'yi bozar — buyutup tekrar dene
    h, w = img.shape[:2]
    for scale in (2.0, 3.0):
        big = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        hit = try_decode(big)
        if hit:
            return hit
    raise ValueError("QR bulunamadi — plakayi yakin/net cekip tekrar gonderin")
