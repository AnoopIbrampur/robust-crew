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

## Results

<!-- RESULTS:START -->
Selected on CALIB days: soft λ = 2 (largest delay reduction with cost ≤ +2%); hard θ = 0.5.

Means per day with 95% bootstrap intervals over days in brackets; (Δ ...) is the paired difference vs the no-repair baseline on the same days, with its 95% interval. Hard robust is evaluated only on days it was feasible (its mean is over those days). Repair = reactive repair (idle-crew swaps + up to 4 reserves); reserves called count as crews used. Intervals over very few days are unreliable.

**All test days, test summer**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 111.3 [109.9, 112.7] | 111.5 (+0.1 [+0.0, +0.2]) | 111.7 (+0.2 [+0.0, +0.3]) | 114.5 (+3.1 [+2.8, +3.4]) | 114.0 (+2.7 [+2.3, +3.0]) |
| Cost (pay hours) | 1176.9 [1155.7, 1195.5] | 1179.1 (+2.2 [+1.8, +2.7]) | 1179.2 (+0.8 [+0.2, +1.7]) | 1176.9 (+0.0 [+0.0, +0.0]) | 1179.1 (+2.2 [+1.8, +2.7]) |
| Crew-caused delay (min) | 2567.9 [1825.9, 3462.9] | 2201.8 (-366.2 [-556.1, -197.4]) | 2454.4 (+17.4 [-108.9, +143.4]) | 728.7 (-1839.3 [-2691.7, -1193.4]) | 445.5 (-2122.4 [-3001.9, -1424.3]) |
| Broken crew connections | 45.3 [40.8, 50.0] | 38.5 (-6.8 [-7.7, -5.8]) | 42.0 (+0.2 [-0.7, +1.1]) | 38.7 (-6.7 [-7.4, -5.9]) | 33.9 (-11.4 [-12.6, -10.3]) |
| Duty-limit violations | 11.7 [9.9, 13.7] | 12.1 (+0.4 [-0.2, +0.9]) | 10.8 (+0.0 [-0.6, +0.5]) | 10.2 (-1.5 [-1.8, -1.2]) | 10.8 (-0.9 [-1.5, -0.3]) |

Days: 62; hard feasible on 32 (52%).

**All test days, test winter**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 106.0 [101.6, 109.9] | 106.3 (+0.3 [+0.2, +0.4]) | 105.7 (+0.1 [+0.0, +0.2]) | 107.5 (+1.5 [+1.2, +1.9]) | 107.5 (+1.5 [+1.2, +1.9]) |
| Cost (pay hours) | 1136.6 [1086.5, 1181.2] | 1139.3 (+2.8 [+2.2, +3.3]) | 1120.3 (+0.4 [+0.0, +1.0]) | 1136.6 (+0.0 [+0.0, +0.0]) | 1139.3 (+2.8 [+2.2, +3.3]) |
| Crew-caused delay (min) | 1021.5 [585.1, 1583.5] | 847.4 (-174.2 [-503.2, +81.3]) | 806.3 (-290.9 [-879.5, +17.4]) | 208.1 (-813.4 [-1355.8, -396.2]) | 178.5 (-843.0 [-1381.2, -420.8]) |
| Broken crew connections | 26.5 [23.3, 29.7] | 20.8 (-5.7 [-6.8, -4.6]) | 20.4 (-0.6 [-1.3, +0.0]) | 23.1 (-3.4 [-4.1, -2.7]) | 18.6 (-7.9 [-9.2, -6.7]) |
| Duty-limit violations | 4.7 [3.6, 5.9] | 5.0 (+0.4 [-0.0, +0.8]) | 3.8 (+0.0 [-0.3, +0.3]) | 4.2 (-0.4 [-0.7, -0.2]) | 4.6 (-0.1 [-0.5, +0.3]) |

Days: 61; hard feasible on 27 (44%).

**10 worst-weather days, test summer**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 109.7 [106.9, 112.2] | 109.7 (+0.0 [+0.0, +0.0]) | 110.7 (+0.3 [+0.0, +1.0]) | 113.3 (+3.6 [+3.2, +4.0]) | 112.5 (+2.8 [+1.9, +3.6]) |
| Cost (pay hours) | 1160.3 [1129.0, 1191.6] | 1162.0 (+1.6 [+1.2, +2.1]) | 1177.9 (+1.9 [+0.0, +4.9]) | 1160.3 (+0.0 [+0.0, +0.0]) | 1162.0 (+1.6 [+1.1, +2.1]) |
| Crew-caused delay (min) | 2493.3 [1497.2, 3538.5] | 2168.5 (-324.8 [-550.9, -84.6]) | 3422.0 (+333.7 [-108.0, +1150.0]) | 1214.2 (-1279.1 [-2153.3, -543.2]) | 607.7 (-1885.6 [-2948.2, -967.0]) |
| Broken crew connections | 63.7 [53.6, 76.3] | 55.9 (-7.8 [-9.7, -5.9]) | 73.7 (+3.3 [-2.0, +7.0]) | 56.9 (-6.8 [-8.7, -4.9]) | 51.7 (-12.0 [-14.9, -9.4]) |
| Duty-limit violations | 21.0 [15.4, 26.8] | 21.0 (+0.0 [-1.6, +1.5]) | 19.7 (-1.0 [-2.0, +0.0]) | 19.4 (-1.6 [-2.2, -1.0]) | 19.6 (-1.4 [-3.1, +0.2]) |

Days: 10; hard feasible on 3 (30%).

**10 worst-weather days, test winter**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 112.2 [104.0, 123.3] | 112.5 (+0.3 [+0.0, +0.6]) | 138.0 (+0.0 [+0.0, +0.0]) | 114.8 (+2.6 [+1.6, +3.5]) | 115.1 (+2.9 [+1.9, +3.9]) |
| Cost (pay hours) | 1212.1 [1127.3, 1316.0] | 1214.9 (+2.9 [+1.3, +4.7]) | 1454.3 (+0.4 [+0.0, +0.8]) | 1212.1 (+0.0 [+0.0, +0.0]) | 1214.9 (+2.9 [+1.3, +4.6]) |
| Crew-caused delay (min) | 2502.6 [659.4, 5113.8] | 1422.6 (-1080.0 [-2713.8, -49.0]) | 2285.5 (-3890.0 [-7766.0, -14.0]) | 235.0 (-2267.6 [-4849.0, -435.1]) | 179.5 (-2323.1 [-4929.2, -491.5]) |
| Broken crew connections | 45.0 [34.8, 53.6] | 37.0 (-8.0 [-11.5, -4.7]) | 30.0 (-3.5 [-6.0, -1.0]) | 39.3 (-5.7 [-7.7, -3.5]) | 32.3 (-12.7 [-16.2, -9.2]) |
| Duty-limit violations | 10.1 [6.3, 14.9] | 10.7 (+0.6 [+0.0, +1.2]) | 11.0 (+0.0 [-1.0, +1.0]) | 8.9 (-1.2 [-2.3, -0.3]) | 9.2 (-0.9 [-1.8, -0.1]) |

Days: 10; hard feasible on 2 (20%).

**Does robust planning add anything once repair exists?** Best soft + repair minus baseline + repair, paired by day (crew-caused delay, min/day; broken connections/day):

| Window | Days | Δ delay | Δ broken connections | Δ crews used |
|---|---|---|---|---|
| test summer | 62 | -283.1 [-491.8, -121.2] | -4.8 [-5.7, -4.0] | -0.5 [-0.7, -0.2] |
| test winter | 61 | -29.5 [-45.3, -16.0] | -4.6 [-5.4, -3.7] | +0.0 [-0.2, +0.2] |
| test summer (worst-weather days) | 10 | -606.5 [-1482.5, -74.6] | -5.2 [-7.3, -3.3] | -0.8 [-1.7, +0.1] |
| test winter (worst-weather days) | 10 | -55.5 [-137.9, +14.2] | -7.0 [-9.4, -4.8] | +0.3 [-0.4, +0.9] |
<!-- RESULTS:END -->
