# Golden replay fixtures (P2-14)

Drop a real day of diag samples here to lock the Schmitt counter against
regressions on production data:

- `AF4_2026-09-02.csv` — a `samples.csv` copied from
  `logs/diag/machine_<id>/<day>/samples.csv` (semicolon-delimited).
- `AF4_2026-09-02.expected.json` — the expected result, e.g.:

  ```json
  {
    "closed_polarity": "low",
    "closed_ref": 0.40,
    "closed_hyst": 0.05,
    "smooth_win": 5,
    "learn_enabled": true,
    "expected_count": 512,
    "tolerance_pct": 2.0
  }
  ```

`test_golden_replay` picks up every `*.expected.json` here automatically and
asserts the replayed count is within `tolerance_pct` of `expected_count`.
Until a fixture is added the test is skipped, so CI stays green.

These CSVs are captured continuously for AF-4/5/6 (14-day retention); copy the
day you want to pin.
