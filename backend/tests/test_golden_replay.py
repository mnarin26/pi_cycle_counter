"""P2-14 — golden-file regression on real diag CSV.

Skipped until a fixture pair is dropped in ``tests/golden/`` (see README there).
Each ``<name>.expected.json`` must have a sibling ``<name>.csv`` (a real
``samples.csv``); the replayed Schmitt count must land within tolerance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.vision.schmitt_counter import SchmittConfig, SchmittCounter

GOLDEN = Path(__file__).resolve().parent / "golden"


def _iter_csv(path: Path):
    with path.open("r", encoding="utf-8", newline="") as fh:
        first = fh.readline()
        if not first:
            return
        delim = ";" if ";" in first else ","
        fh.seek(0)
        import csv as csv_mod

        for row in csv_mod.DictReader(fh, delimiter=delim):
            k = (row.get("k") or "").strip()
            if k == "stale_skip":
                continue
            mono_raw = (row.get("mono") or "").strip()
            if not mono_raw:
                continue
            try:
                mono = float(mono_raw)
            except ValueError:
                continue
            pos_raw = (row.get("pos") or "").strip()
            pos = None if not pos_raw else float(pos_raw)
            yield mono, pos


def _cases():
    if not GOLDEN.is_dir():
        return []
    return sorted(GOLDEN.glob("*.expected.json"))


@pytest.mark.parametrize(
    "spec_path", _cases(), ids=lambda p: getattr(p, "stem", "none")
)
def test_golden_replay(spec_path: Path):
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    csv_path = spec_path.with_name(spec_path.name.replace(".expected.json", ".csv"))
    if not csv_path.exists():
        pytest.skip(f"missing CSV for {spec_path.name}")

    c = SchmittCounter(
        SchmittConfig(
            closed_polarity=spec.get("closed_polarity", "low"),
            closed_ref=spec.get("closed_ref"),
            closed_hyst=float(spec.get("closed_hyst", 0.05)),
            smooth_win=int(spec.get("smooth_win", 5)),
            learn_enabled=bool(spec.get("learn_enabled", True)),
        )
    )
    last = None
    for mono, pos in _iter_csv(csv_path):
        if last is not None and mono + 0.05 < last:
            c.reset()
        last = mono
        c.step(pos, mono)

    expected = int(spec["expected_count"])
    tol = max(1, int(expected * float(spec.get("tolerance_pct", 2.0)) / 100.0))
    assert abs(c.count - expected) <= tol, (
        f"count={c.count} expected={expected} tol=±{tol}"
    )


def test_golden_dir_exists():
    # The fixture directory ships with the repo even when empty of CSVs.
    assert GOLDEN.is_dir()
