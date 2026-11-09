# robust-crew

Delay-robust airline crew pairing. A classical set-partitioning optimizer (OR-Tools CP-SAT) over enumerated one-day pairings, extended with a machine-learned connection-break risk model, and evaluated by replaying the delays that actually happened (BTS on-time data).

**Status:** Phase 1 (data ingestion) done; see [reports/phase1_data_quality.md](reports/phase1_data_quality.md). Scope: American Airlines (operating carrier) at its Charlotte (CLT) hub.

## Reproduce Phase 1

```bash
uv sync
uv run python -m robust_crew.ingest all
uv run python -m robust_crew.quality
```

> Crews are synthetic and crew rules are a simplified model inspired by FAA Part 117. Nothing here is a claim of legal or contractual compliance.

## Evaluation windows

| Split | Dates | Use |
|---|---|---|
| train | 2022-01-01 to 2023-04-30 | delay model fitting, historical route stats |
| calib | 2023-05-01 to 2023-06-30 | probability calibration / validation |
| test_summer | 2023-07-01 to 2023-08-31 | planning + replay (thunderstorm season) |
| gap | 2023-09-01 to 2023-10-31 | unused |
| test_winter | 2023-11-01 to 2023-12-31 | planning + replay (originally planned window) |

The original plan had a single Nov-Dec 2023 test window. Phase 1 showed it was unusually calm for AA (see the data quality report), so a summer window was added, chosen by season rather than by its observed delays. The original window is kept and reported, so this change cannot hide an unfavourable result.
