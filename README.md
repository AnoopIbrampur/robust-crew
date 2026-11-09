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
