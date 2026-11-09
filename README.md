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

## Modeling decisions (so far)

- **Planning flight set.** AA flights departing from or arriving at CLT on a local date. Spoke-to-spoke flights belong to other bases. Cancelled flights stay in the plan (a planner cannot know them in advance); the replay drops them on the day.
- **Crew rules.** `rules.py` uses the FAA Part 117 Table A (flight time) and Table B (flight duty period) values keyed by report time in Eastern time; connection minimums (30 min same aircraft, 45 min aircraft change), the 1 h report / 30 min release buffers, max 6 legs and max 4 h sit are planning conventions. Not a legal-compliance claim.
- **One-day pairings leave many flights uncovered.** Evening departures from CLT and early-morning arrivals into CLT need an overnight at the spoke, and long flights break the duty limits. Each day's dropped flights and their reasons are in `reports/phase3_pairings.json`.
- **Plannable set.** Being in some legal pairing does not guarantee an exact cover exists (every one-day pairing flies as many legs out of CLT as into it). Each day's plannable set is the largest set of flights that some set of pairings covers exactly once (CP-SAT set packing, cost as tie-break). Every planner covers exactly this set, so schedules are compared on the same flights.
- **Pairing enumeration** is a depth-first search with forward checking; a brute-force test confirms it finds exactly the legal pairings on small instances. Per-day counts and timings: `reports/phase3_pairings_by_day.csv`.
- **Thanksgiving** (2022-11-24, 2023-11-23) shows only morning CLT departures in BTS, so very few pairings close; flagged as outliers in evaluation.
- **Same-aircraft flag** uses BTS `Tail_Number`, which is the aircraft that actually flew (including day-of swaps); no public source has the planned tail.
- **Connection-break model** (`delay_model.py`): LightGBM on planning features, isotonic-calibrated on CALIB, evaluated on both test windows against two baselines (slack-only logistic regression; the route's train-window probability of exceeding the slack). Numbers: `reports/phase4_delay_model.json`; reliability diagram: `reports/figures/break_model_calibration.png`. Most of the model's lift over the baselines comes from aircraft-rotation features (the tail's scheduled turn before the leg), which inherit the actual-vs-planned tail caveat above; `python -m robust_crew.ablation_tail` reports the model without them.
- **Probabilities are calibrated to the calibration window's regime** (May-Jun 2023). Planning features cannot see a test day's weather, so in the stormier summer window the model under-predicts the break rate and in the calm winter window it over-predicts; ranking (AUC) holds up in both.
- **Planners** (`optimize.py`): CP-SAT set partitioning over each day's plannable set. Baseline and soft-robust solve every CALIB and test day to proven optimality. The hard-robust planner as originally specified (forbid any pairing with a connection at p >= theta) was infeasible on nearly every day; by explicit decision, legs with no pairing below theta are exempted (their least risky pairings re-allowed, exemptions logged). Even so, it remains infeasible on roughly half the days at theta = 0.5 and almost all days below 0.25, because removing risky pairings leaves no exact cover. Summary: `reports/phase5_planners.json`. Lambda and theta are chosen on CALIB days, never on test days.
- **Replay simulator** (`simulate.py`): re-flies the whole AA network each day in scheduled order; a flight leaves at max(scheduled + own delay, aircraft ready, crew ready). Own departure delay is `DepDelay - LateAircraftDelay` (the real airline's aircraft propagation is removed and re-created by the simulator, so it is not counted twice); real crew delays inside `CarrierDelay` cannot be separated and stay in the own delay, identical for every planner. Crew-caused delay is measured against a no-crew counterfactual replay, so knock-on delay through aircraft counts. Cancelled and diverted flights are dropped from the plan; a pairing whose station chain breaks gives its next leg no crew constraint (counted as orphaned). A hand-computed 4-flight day is in `tests/test_simulate.py`. Validation of the no-crew replay against real arrival delays and all replay metrics: `reports/phase6_replay.json`.
- **Duty-limit violations** in the replay compare simulated FDP with the Table B limit; real operations would use extensions or reserves, which this model does not.
