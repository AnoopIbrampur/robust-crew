# robust-crew

**Delay-robust airline crew scheduling, tested by replaying the delays that actually happened.**

A crew schedule that is optimal on scheduled times is fragile: crews connect through tight turns, and one late inbound flight ripples through the rest of their day. This project asks whether you can buy robustness cheaply by (1) predicting the risk that each crew connection breaks, (2) feeding that risk into a classical set-partitioning optimizer, and (3) judging the result by re-flying real days of American Airlines operations at Charlotte (CLT) with their real delays. The deliverable is the trade-off curve between crews and propagated delay, not one number.

It extends a class report on constraint satisfaction for airline scheduling: the CSP core (pairing enumeration with forward checking) is the same idea, with machine learning and an honest out-of-sample replay added around it.

> **Crews are synthetic and crew rules are simplified.** The duty and flight-time limits use the values of FAA 14 CFR Part 117 Tables A and B, but rest rules, cumulative limits, extensions, deadheads and all contractual rules are omitted. Nothing here is a claim of legal or contractual compliance. Flights, aircraft rotations, delays and weather are real.

## Results

<!-- RESULTS:START -->
- **test summer** (62 days): with no repair, the selected soft-robust plan cuts crew-caused delay from 2448 to 2174 min/day (-11%; paired Δ -274 [-488, -33]) and broken connections from 44.7 to 38.6 for +0.1 crews/day. Reactive repair alone takes the baseline to 535 min/day; robust planning plus repair reaches 441 (paired Δ vs repair alone -94 [-209, +14] min/day).
- **test winter** (61 days): with no repair, the selected soft-robust plan cuts crew-caused delay from 1043 to 848 min/day (-19%; paired Δ -195 [-523, +60], interval includes zero) and broken connections from 26.3 to 20.8 for +0.3 crews/day. Reactive repair alone takes the baseline to 310 min/day; robust planning plus repair reaches 184 (paired Δ vs repair alone -126 [-316, -22] min/day).

- **Operations features barely help.** The morning-of model's AUC gain over the planning model: test summer +0.007, test winter +0.008.
- **Morning re-solve (test summer)**, the one repair step that uses operations probabilities: Δ crew-caused delay vs no re-solve -72.4 [-213.6, +122.0] min/day on the baseline plan and -60.6 [-173.8, +23.2] on the selected soft plan (each without reactive repair). Reactive repair, which drives the headline effect, does not use the model's probabilities.
- **Morning re-solve (test winter)**, the one repair step that uses operations probabilities: Δ crew-caused delay vs no re-solve -87.9 [-148.4, -42.5] min/day on the baseline plan and -9.0 [-25.7, +8.5] on the selected soft plan (each without reactive repair). Reactive repair, which drives the headline effect, does not use the model's probabilities.
- **Excluding Thanksgiving (test winter, 60 days):** soft vs baseline Δ delay -198.6 [-542.7, +65.9]; soft + repair vs baseline + repair -128.5 [-320.4, -22.9] min/day.
- **Baseline tie-breaking (test summer):** re-solving the baseline with 5 different choices among cost-tied optima gives mean crew-caused delay between 2370 and 2571 min/day (largest cost difference 0.00 pay hours, i.e. equally cheap). The selected soft plan's 2174 beats every one of them, so its gain is more than lucky tie-breaking; but part of the cheap low-λ gain is simply choosing well among schedules that cost the same.
- **Baseline tie-breaking (test winter):** re-solving the baseline with 5 different choices among cost-tied optima gives mean crew-caused delay between 898 and 991 min/day (largest cost difference 0.02 pay hours, i.e. equally cheap). The selected soft plan's 848 beats every one of them, so its gain is more than lucky tie-breaking; but part of the cheap low-λ gain is simply choosing well among schedules that cost the same.

![Trade-off curve](reports/figures/tradeoff.png)

![Worst-weather days](reports/figures/worst_days.png)

<details><summary>Full tables with confidence intervals</summary>

Selected on CALIB days: soft λ = 2 (largest delay reduction with cost ≤ +2%); hard θ = 0.5.

Means per day with 95% bootstrap intervals over days in brackets; (Δ ...) is the paired difference vs the no-repair baseline on the same days, with its 95% interval. Hard robust is evaluated only on days it was feasible (its mean is over those days). Repair = reactive repair (idle-crew swaps + up to 4 reserves); reserves called count as crews used, but the cost column excludes reserve pay (reserves are on standby payroll either way). Intervals over very few days are unreliable.

**All test days, test summer**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 111.3 [109.9, 112.7] | 111.5 (+0.1 [+0.0, +0.2]) | 111.7 (+0.2 [+0.0, +0.3]) | 114.4 (+3.1 [+2.8, +3.3]) | 113.8 (+2.5 [+2.1, +2.8]) |
| Cost (pay hours) | 1176.9 [1155.7, 1195.5] | 1179.1 (+2.2 [+1.8, +2.7]) | 1179.2 (+0.8 [+0.2, +1.7]) | 1176.9 (+0.0 [+0.0, +0.0]) | 1179.1 (+2.2 [+1.8, +2.7]) |
| Crew-caused delay (min) | 2447.7 [1728.3, 3342.1] | 2173.5 (-274.2 [-487.6, -33.4]) | 2425.6 (+29.8 [-91.3, +166.4]) | 534.7 (-1912.9 [-2786.4, -1257.4]) | 441.1 (-2006.6 [-2906.1, -1306.6]) |
| Broken crew connections | 44.7 [40.4, 49.4] | 38.6 (-6.1 [-7.0, -5.3]) | 42.3 (+0.8 [-0.1, +1.6]) | 37.8 (-6.9 [-7.7, -6.2]) | 33.9 (-10.8 [-11.9, -9.7]) |
| Duty-limit violations | 11.5 [9.8, 13.3] | 11.8 (+0.3 [-0.2, +0.9]) | 10.5 (-0.3 [-0.9, +0.2]) | 10.2 (-1.4 [-1.6, -1.1]) | 10.8 (-0.7 [-1.2, -0.1]) |

Days: 62; hard feasible on 32 (52%).

**All test days, test winter**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 106.0 [101.6, 109.9] | 106.3 (+0.3 [+0.2, +0.4]) | 105.7 (+0.1 [+0.0, +0.2]) | 107.5 (+1.5 [+1.1, +1.9]) | 107.5 (+1.5 [+1.1, +1.8]) |
| Cost (pay hours) | 1136.6 [1086.5, 1181.2] | 1139.3 (+2.8 [+2.2, +3.3]) | 1120.3 (+0.5 [+0.0, +1.1]) | 1136.6 (+0.0 [+0.0, +0.0]) | 1139.3 (+2.8 [+2.2, +3.3]) |
| Crew-caused delay (min) | 1043.5 [612.0, 1606.4] | 848.2 (-195.3 [-523.1, +59.9]) | 898.5 (-212.2 [-716.7, +59.0]) | 310.1 (-733.4 [-1244.7, -350.5]) | 183.7 (-859.8 [-1393.9, -436.9]) |
| Broken crew connections | 26.3 [23.2, 29.7] | 20.8 (-5.5 [-6.5, -4.5]) | 21.2 (+0.3 [-0.1, +0.9]) | 22.9 (-3.4 [-4.2, -2.6]) | 18.6 (-7.7 [-9.0, -6.5]) |
| Duty-limit violations | 4.5 [3.4, 5.8] | 5.1 (+0.6 [+0.2, +1.0]) | 3.4 (-0.1 [-0.3, +0.2]) | 4.1 (-0.4 [-0.7, -0.2]) | 4.6 (+0.1 [-0.3, +0.5]) |

Days: 61; hard feasible on 27 (44%).

**10 worst-weather days, test summer**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 109.7 [106.9, 112.2] | 109.7 (+0.0 [+0.0, +0.0]) | 110.7 (+0.3 [+0.0, +1.0]) | 113.1 (+3.4 [+3.0, +3.8]) | 112.0 (+2.3 [+1.4, +3.2]) |
| Cost (pay hours) | 1160.3 [1129.0, 1191.6] | 1162.0 (+1.6 [+1.2, +2.1]) | 1177.9 (+1.9 [+0.0, +4.9]) | 1160.3 (+0.0 [+0.0, +0.0]) | 1162.0 (+1.6 [+1.1, +2.1]) |
| Crew-caused delay (min) | 2032.6 [1291.7, 2808.3] | 2307.9 (+275.3 [-412.4, +1339.9]) | 3027.3 (-167.0 [-783.0, +168.0]) | 517.1 (-1515.5 [-2359.0, -791.2]) | 695.3 (-1337.3 [-2268.6, -565.8]) |
| Broken crew connections | 62.2 [52.0, 74.9] | 56.5 (-5.7 [-7.9, -3.6]) | 72.7 (+2.0 [-3.0, +5.0]) | 55.4 (-6.8 [-8.1, -5.4]) | 52.2 (-10.0 [-12.8, -7.7]) |
| Duty-limit violations | 20.4 [15.4, 25.6] | 20.9 (+0.5 [-1.4, +2.3]) | 18.7 (-1.7 [-3.0, -1.0]) | 18.7 (-1.7 [-2.1, -1.3]) | 20.0 (-0.4 [-2.2, +1.3]) |

Days: 10; hard feasible on 3 (30%).

**10 worst-weather days, test winter**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 112.2 [104.0, 123.3] | 112.5 (+0.3 [+0.0, +0.6]) | 138.0 (+0.0 [+0.0, +0.0]) | 115.0 (+2.8 [+1.6, +4.0]) | 115.0 (+2.8 [+1.7, +3.9]) |
| Cost (pay hours) | 1212.1 [1127.3, 1316.0] | 1214.9 (+2.9 [+1.3, +4.7]) | 1454.3 (+0.4 [+0.0, +0.8]) | 1212.1 (+0.0 [+0.0, +0.0]) | 1214.9 (+2.9 [+1.3, +4.6]) |
| Crew-caused delay (min) | 2668.5 [813.1, 5218.1] | 1419.9 (-1248.6 [-2866.2, -183.6]) | 2920.0 (-3208.5 [-6516.0, +99.0]) | 873.3 (-1795.2 [-4236.3, -299.7]) | 211.9 (-2456.6 [-5040.5, -626.6]) |
| Broken crew connections | 45.0 [34.0, 54.5] | 36.7 (-8.3 [-11.9, -4.6]) | 33.5 (+1.0 [-3.0, +5.0]) | 39.1 (-5.9 [-8.3, -3.4]) | 32.3 (-12.7 [-16.7, -8.5]) |
| Duty-limit violations | 10.4 [5.9, 15.7] | 10.9 (+0.5 [-0.7, +1.6]) | 12.5 (-0.5 [-1.0, +0.0]) | 9.0 (-1.4 [-2.6, -0.4]) | 9.5 (-0.9 [-2.3, +0.3]) |

Days: 10; hard feasible on 2 (20%).

</details>

**Does robust planning add anything once repair exists?** Best soft + repair minus baseline + repair, paired by day (crew-caused delay, min/day; broken connections/day):

| Window | Days | Δ delay | Δ broken connections | Δ crews used |
|---|---|---|---|---|
| test summer | 62 | -93.7 [-209.5, +14.1] | -3.9 [-4.6, -3.2] | -0.6 [-0.9, -0.3] |
| test winter | 61 | -126.4 [-316.1, -22.3] | -4.3 [-5.2, -3.5] | +0.0 [-0.2, +0.2] |
| test summer (worst-weather days) | 10 | +178.2 [-89.1, +516.8] | -3.2 [-5.4, -1.0] | -1.1 [-2.0, -0.3] |
| test winter (worst-weather days) | 10 | -661.4 [-1735.4, -42.4] | -6.8 [-9.5, -4.1] | +0.0 [-0.8, +0.7] |
<!-- RESULTS:END -->

**Reading the results.** All numbers above are generated; this paragraph only says how to read them. Robust planning on its own cuts broken connections and crew-caused delay at almost no extra cost. Day-of repair (swapping crews and calling reserves) is the bigger lever: it removes most crew-caused delay whatever the plan. Whether robust planning still adds to repair is answered by the paired table at the end of the details: it keeps cutting broken connections, while its extra effect on delay minutes is smaller and its intervals are wide, because delay totals are dominated by a few bad days. The hard-constraint planner does not work well here, and the operations-feature machinery (morning model and re-solve) adds little (see limitations).

## Approach

```mermaid
flowchart LR
    A[BTS on-time data<br/>2022-2023, real tails] --> B[UTC flights + aircraft rotations]
    W[IEM ASOS weather] --> F
    B --> C[Connection graph<br/>Part 117 rules]
    C --> D[DFS pairing enumeration<br/>forward checking]
    B --> F[Features<br/>planning vs operations]
    F --> M[LightGBM break-risk model<br/>isotonic calibration]
    D --> O[CP-SAT set partitioning<br/>baseline / soft / hard]
    M --> O
    O --> S[Replay real day<br/>aircraft + crew propagation]
    S --> R[Reactive repair<br/>swaps + reserves]
    S --> E[Trade-off curves<br/>bootstrap CIs]
    R --> E
```

1. **Data** (`ingest.py`). 24 months of BTS Reporting Carrier On-Time Performance (every month's row count checked against the raw file), converted to UTC by anchoring on the scheduled departure and adding BTS durations, plus hourly ASOS weather for CLT and its top destinations. Report: [reports/phase1_data_quality.md](reports/phase1_data_quality.md).
2. **Network and rules** (`network.py`, `rules.py`). Crew connections need 30 min (same aircraft) or 45 min (aircraft change); duty limits follow Part 117 Table B by report time and segments, with a 1 h report and 30 min release. Every number lives in `rules.py`.
3. **Pairings, the CSP** (`pairings.py`). Depth-first search from CLT with forward checking enumerates every legal one-day pairing (a brute-force test confirms it finds exactly the legal set). Cost is pay hours with a 5 h guarantee plus a fixed per-crew cost.
4. **Break-risk model** (`delay_model.py`). A connection breaks if the inbound's actual arrival plus the minimum connection is later than the outbound's scheduled departure. LightGBM on planning features (scheduled slack, stations, hours, aircraft rotation, hub congestion, train-window route history computed out of fold), calibrated on a held-out window; a separate operations model adds morning weather and delays observed so far.
5. **Planners** (`optimize.py`). CP-SAT set partitioning: baseline (min cost), soft robust (cost + λ × Σ break probability × legs after the connection), hard robust (forbid connections at p ≥ θ).
6. **Replay** (`simulate.py`). Re-flies the whole AA network each day: a flight leaves at max(scheduled + own delay, aircraft ready, crew ready). The real airline's own aircraft propagation is removed from each delay (`DepDelay − LateAircraftDelay`) and re-created by the simulator, so it is not counted twice. Crew-caused delay is measured against a no-crew counterfactual, so knock-on delay through aircraft counts.
7. **Repair** (`repair.py`). When a crew would delay its next CLT departure by more than 15 min or break its duty limit, swap the rest of its day with an idle crew or hand it to one of 4 reserves. A morning CP-SAT re-solve with operations probabilities was also tested.
8. **Experiments** (`experiments.py`). λ and θ chosen on calibration days only; means with 95% bootstrap intervals over days; hard robust compared with the baseline on the same days.

## Reproduce

```bash
git clone https://github.com/AnoopIbrampur/robust-crew && cd robust-crew
uv sync
uv run python -m robust_crew.pipeline
```

The pipeline downloads 24 monthly BTS files (zips are deleted after loading), then writes every table, report and figure. `uv run pytest` runs the test suite. Every number in this README is generated by `experiments.py` from the stored results.

## Explorer app

```bash
uv sync --extra app
uv run streamlit run app/streamlit_app.py
```

Pick a season, a day (worst first) and whether day-of repair runs; the robustness weight λ sits under Settings. The app replays that day live, compares the robust plan with the cheapest plan in metric cards and one plain-language summary, and shows each crew's day as a timeline: grey is the schedule, colour the replay, by crew-caused delay including knock-on through the aircraft. A list shows every flight that waited for a late crew. Run it from the repository root so `.streamlit/config.toml` (light theme) applies; it needs the local database built by the pipeline.

## Evaluation design

| Split | Dates | Use |
|---|---|---|
| train | 2022-01-01 to 2023-04-30 | model fitting, historical route statistics |
| calib | 2023-05-01 to 2023-06-30 | probability calibration; choosing λ and θ |
| test_summer | 2023-07-01 to 2023-08-31 | planning + replay (thunderstorm season) |
| gap | 2023-09-01 to 2023-10-31 | unused |
| test_winter | 2023-11-01 to 2023-12-31 | planning + replay (originally planned window) |

The original plan had one Nov-Dec 2023 test window. Phase 1 showed it was unusually calm for AA (see the data quality report), so a summer window was added, chosen by season rather than by its observed delays. The original window is kept and reported so this change cannot hide an unfavourable result. A leakage test asserts no training table contains calibration or test dates; operations features use only information before 07:00 Eastern on the day.

## Limitations (honest list)

- **Synthetic crews, simplified rules.** No rest, cumulative or contractual rules; no deadheads; reserves are idealised airport standby at the hub. Replay duty violations compare with the Table B limit, but real operations would extend or swap crews.
- **One-day pairings only.** Evening departures and early-morning arrivals at CLT need an overnight at the outstation, so a large share of CLT flights cannot be planned at all (per-day shares and reasons in `reports/phase3_pairings.json`). Each day's plannable set is the largest set that can be covered exactly once, and every planner covers that same set.
- **Actual, not planned, aircraft.** BTS `Tail_Number` is the aircraft that flew, including day-of swaps. Most of the break model's lift over simple baselines comes from aircraft-rotation features, so it leans on actual tails ≈ planned tails (`python -m robust_crew.ablation_tail` reports the model without them).
- **Probabilities drift with the season.** Calibrated on May-June, the model under-predicts breaks in the stormy summer and over-predicts in the calm winter; ranking holds up.
- **The hard-robust planner mostly fails.** Forbidding risky connections outright leaves no exact cover on most days, even after exempting legs that have no low-risk option; it is evaluated only on its feasible days, where it gains little. Soft penalties are the better tool here.
- **No passenger connections, no cancellations decisions, one hub.** Delay is measured in flight minutes, not passenger minutes; cancelled flights are taken as given.
- **Repair knows the inbound's arrival** when it decides (the aircraft is airborne), and repairs only at the hub.
- **Thanksgiving** shows only morning CLT departures in BTS, so very few pairings close on those days.

## What I would do next

- **Column generation** instead of full enumeration, to handle multi-day pairings with overnights and cover the flights one-day pairings cannot.
- **Multi-day pairings and rest rules**, which also make the rules closer to real Part 117.
- **Seasonal recalibration** (calibrate each test window on the same months of the previous year) and a model of tail swaps.
- **Joint planning of reserves and robustness**: the results suggest the cheapest robustness is a slightly robust plan plus a few reserves.
- **Passenger-weighted delay** using itinerary data.

## Repository layout

```
src/robust_crew/
  config.py      scope, date splits, paths        ingest.py      BTS + weather -> DuckDB
  timeconv.py    local -> UTC                     quality.py     Phase 1 report
  rules.py       crew rules (single source)       network.py     legs, connections, rotations
  pairings.py    DFS enumeration, plannable set   delay_model.py break-risk models
  optimize.py    CP-SAT planners                  simulate.py    replay of real days
  repair.py      day-of recovery                  experiments.py results, figures, README block
  leakage.py     train/test guards                pipeline.py    everything, in order
tests/           unit tests incl. hand-computed replay and brute-force enumeration checks
reports/         every generated number and figure
notebooks/       results.ipynb (reads reports/ only)
app/             streamlit_app.py (explorer, optional extra)
```

Data: [BTS Reporting Carrier On-Time Performance](https://www.transtats.bts.gov/DatabaseInfo.asp?QO_VQ=EFD) and [Iowa Environmental Mesonet ASOS](https://mesonet.agron.iastate.edu/request/download.phtml). Part 117 tables: [14 CFR Part 117](https://www.law.cornell.edu/cfr/text/14/part-117).
