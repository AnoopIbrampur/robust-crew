# robust-crew

**Delay-robust airline crew scheduling, tested by replaying the delays that actually happened.**

A crew schedule that is optimal on scheduled times is fragile: crews connect through tight turns, and one late inbound flight ripples through the rest of their day. This project asks whether you can buy robustness cheaply by (1) predicting the risk that each crew connection breaks, (2) feeding that risk into a classical set-partitioning optimizer, and (3) judging the result by re-flying real days of American Airlines operations at Charlotte (CLT) with their real delays. The deliverable is the trade-off curve between crews and propagated delay, not one number.

It extends a class report on constraint satisfaction for airline scheduling: the CSP core (pairing enumeration with forward checking) is the same idea, with machine learning and an honest out-of-sample replay added around it.

> **Crews are synthetic and crew rules are simplified.** The duty and flight-time limits use the values of FAA 14 CFR Part 117 Tables A and B, but rest rules, cumulative limits, extensions, deadheads and all contractual rules are omitted. Nothing here is a claim of legal or contractual compliance. Flights, aircraft rotations, delays and weather are real.

## Results

<!-- RESULTS:START -->
- **test summer** (62 days): with no repair, the selected soft-robust plan cuts crew-caused delay from 711 to 376 min/day (-47%; paired Δ -335 [-411, -264]) and broken connections from 44.2 to 35.2 for +3.9 crews/day. Reactive repair alone takes the baseline to 223 min/day; robust planning plus repair reaches 125 (paired Δ vs repair alone -99 [-151, -55] min/day).
- **test winter** (61 days): with no repair, the selected soft-robust plan cuts crew-caused delay from 325 to 165 min/day (-49%; paired Δ -160 [-243, -97]) and broken connections from 26.1 to 19.3 for +2.8 crews/day. Reactive repair alone takes the baseline to 117 min/day; robust planning plus repair reaches 70 (paired Δ vs repair alone -48 [-76, -25] min/day).

- **Operations features barely help.** The morning-of model's AUC gain over the planning model: test summer +0.007, test winter +0.008.
- **Morning re-solve (test summer)**, the one repair step that uses operations probabilities: Δ crew-caused delay vs no re-solve -203.8 [-277.6, -136.9] min/day on the baseline plan and +8.1 [-31.9, +48.6] on the selected soft plan (each without reactive repair). Reactive repair, which drives the headline effect, does not use the model's probabilities.
- **Morning re-solve (test winter)**, the one repair step that uses operations probabilities: Δ crew-caused delay vs no re-solve -79.5 [-112.7, -50.1] min/day on the baseline plan and +7.8 [-2.1, +19.0] on the selected soft plan (each without reactive repair). Reactive repair, which drives the headline effect, does not use the model's probabilities.
- **Excluding Thanksgiving (test winter, 60 days):** soft vs baseline Δ delay -162.6 [-246.5, -99.8]; soft + repair vs baseline + repair -48.4 [-78.1, -25.4] min/day.
- **Baseline tie-breaking (test summer):** re-solving the baseline with 5 different choices among cost-tied optima gives mean crew-caused delay between 697 and 735 min/day (largest cost difference 0.00 pay hours, i.e. equally cheap). The selected soft plan's 376 beats every one of them, so its gain is more than lucky tie-breaking; but part of the cheap low-λ gain is simply choosing well among schedules that cost the same.
- **Baseline tie-breaking (test winter):** re-solving the baseline with 5 different choices among cost-tied optima gives mean crew-caused delay between 304 and 336 min/day (largest cost difference 0.02 pay hours, i.e. equally cheap). The selected soft plan's 165 beats every one of them, so its gain is more than lucky tie-breaking; but part of the cheap low-λ gain is simply choosing well among schedules that cost the same.

![Trade-off curve](reports/figures/tradeoff.png)

![Worst-weather days](reports/figures/worst_days.png)

<details><summary>Full tables with confidence intervals</summary>

Selected on CALIB days: soft λ = 4 (largest delay reduction with cost ≤ +2%); hard θ = 0.5.

Means per day with 95% bootstrap intervals over days in brackets; (Δ ...) is the paired difference vs the no-repair baseline on the same days, with its 95% interval. Hard robust is evaluated only on days it was feasible (its mean is over those days). Repair = reactive repair (idle-crew swaps + up to 4 reserves); reserves called count as crews used, but the cost column excludes reserve pay (reserves are on standby payroll either way). Intervals over very few days are unreliable.

**All test days, test summer**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 111.3 [109.9, 112.7] | 115.3 (+3.9 [+3.5, +4.3]) | 111.7 (+0.2 [+0.0, +0.3]) | 114.2 (+2.9 [+2.6, +3.1]) | 117.1 (+5.8 [+5.2, +6.3]) |
| Cost (pay hours) | 1176.9 [1155.7, 1195.5] | 1198.8 (+21.9 [+19.9, +23.9]) | 1179.2 (+0.8 [+0.2, +1.6]) | 1176.9 (+0.0 [+0.0, +0.0]) | 1198.8 (+21.9 [+19.9, +23.8]) |
| Crew-caused delay (min) | 711.4 [594.6, 837.7] | 376.2 (-335.3 [-410.6, -264.0]) | 602.3 (-35.5 [-111.9, +29.8]) | 223.2 (-488.2 [-569.6, -410.2]) | 124.7 (-586.7 [-689.4, -489.1]) |
| Broken crew connections | 44.2 [39.9, 48.8] | 35.2 (-8.9 [-10.0, -7.9]) | 41.7 (+0.7 [-0.2, +1.5]) | 37.9 (-6.3 [-7.1, -5.6]) | 31.7 (-12.5 [-13.8, -11.2]) |
| Crews replaced at duty limit | 10.4 [9.1, 11.8] | 10.7 (+0.2 [-0.3, +0.7]) | 9.8 (-0.0 [-0.5, +0.4]) | 9.3 (-1.1 [-1.3, -0.9]) | 9.6 (-0.8 [-1.3, -0.3]) |
| Duty overruns in the air | 2.5 [1.8, 3.2] | 2.4 (-0.1 [-0.6, +0.3]) | 1.9 (-0.4 [-0.9, +0.0]) | 2.2 (-0.2 [-0.4, -0.1]) | 2.3 (-0.1 [-0.6, +0.3]) |

Days: 62; hard feasible on 32 (52%).

**All test days, test winter**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 106.0 [101.4, 109.9] | 108.8 (+2.8 [+2.3, +3.4]) | 105.7 (+0.1 [+0.0, +0.2]) | 107.4 (+1.4 [+1.0, +1.7]) | 109.6 (+3.6 [+3.0, +4.2]) |
| Cost (pay hours) | 1136.6 [1087.0, 1179.7] | 1152.5 (+15.9 [+13.4, +18.5]) | 1120.3 (+0.5 [+0.0, +1.1]) | 1136.6 (+0.0 [+0.0, +0.0]) | 1152.5 (+15.9 [+13.4, +18.5]) |
| Crew-caused delay (min) | 324.9 [244.4, 418.8] | 165.0 (-159.9 [-243.2, -97.3]) | 255.3 (-13.7 [-50.7, +17.3]) | 117.4 (-207.5 [-281.7, -145.5]) | 69.8 (-255.1 [-342.9, -180.9]) |
| Broken crew connections | 26.1 [22.9, 29.4] | 19.3 (-6.8 [-8.0, -5.6]) | 21.0 (+0.3 [-0.1, +0.9]) | 22.9 (-3.2 [-3.9, -2.5]) | 17.7 (-8.4 [-9.8, -7.0]) |
| Crews replaced at duty limit | 4.2 [3.4, 5.2] | 4.9 (+0.6 [+0.2, +1.1]) | 3.4 (-0.1 [-0.4, +0.1]) | 3.7 (-0.6 [-0.8, -0.3]) | 4.5 (+0.3 [-0.1, +0.7]) |
| Duty overruns in the air | 1.3 [0.9, 1.8] | 1.5 (+0.1 [-0.1, +0.4]) | 0.9 (+0.0 [-0.1, +0.2]) | 1.3 (-0.0 [-0.1, +0.0]) | 1.4 (+0.1 [-0.2, +0.4]) |

Days: 61; hard feasible on 27 (44%).

**10 worst-weather days, test summer**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 109.7 [106.9, 112.2] | 114.1 (+4.4 [+3.7, +5.2]) | 110.7 (+0.3 [+0.0, +1.0]) | 112.8 (+3.1 [+2.6, +3.6]) | 116.0 (+6.3 [+5.1, +7.6]) |
| Cost (pay hours) | 1160.3 [1128.1, 1191.6] | 1184.5 (+24.1 [+20.2, +28.5]) | 1177.9 (+1.9 [+0.0, +4.9]) | 1160.3 (+0.0 [+0.0, +0.0]) | 1184.5 (+24.1 [+20.2, +28.4]) |
| Crew-caused delay (min) | 690.4 [501.6, 906.9] | 366.6 (-323.8 [-437.9, -214.7]) | 684.0 (+66.0 [-80.0, +168.0]) | 221.5 (-468.9 [-622.5, -331.6]) | 166.8 (-523.6 [-705.6, -357.1]) |
| Broken crew connections | 61.8 [51.5, 74.8] | 52.5 (-9.3 [-12.1, -6.9]) | 72.0 (+2.0 [-2.0, +5.0]) | 55.7 (-6.1 [-7.5, -4.8]) | 49.1 (-12.7 [-16.3, -9.8]) |
| Crews replaced at duty limit | 15.9 [12.1, 20.2] | 16.1 (+0.2 [-1.0, +1.3]) | 15.7 (-1.7 [-3.0, -1.0]) | 14.7 (-1.2 [-1.6, -0.7]) | 14.6 (-1.3 [-2.4, +0.0]) |
| Duty overruns in the air | 5.2 [3.0, 7.7] | 6.0 (+0.8 [-0.5, +2.2]) | 3.7 (+0.0 [-2.0, +2.0]) | 4.8 (-0.4 [-0.8, +0.0]) | 6.0 (+0.8 [-0.6, +2.2]) |

Days: 10; hard feasible on 3 (30%).

**10 worst-weather days, test winter**

| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |
|---|---|---|---|---|---|
| Crews used | 112.2 [104.0, 123.2] | 116.2 (+4.0 [+2.7, +5.3]) | 138.0 (+0.0 [+0.0, +0.0]) | 114.8 (+2.6 [+1.5, +3.6]) | 118.0 (+5.8 [+4.1, +7.3]) |
| Cost (pay hours) | 1212.1 [1128.5, 1319.7] | 1234.3 (+22.2 [+15.1, +29.5]) | 1454.3 (+0.4 [+0.0, +0.8]) | 1212.1 (+0.0 [+0.0, +0.0]) | 1234.3 (+22.2 [+15.0, +29.4]) |
| Crew-caused delay (min) | 771.9 [433.3, 1153.6] | 295.3 (-476.6 [-881.5, -156.5]) | 537.0 (-108.5 [-316.0, +99.0]) | 271.7 (-500.2 [-801.0, -235.8]) | 93.8 (-678.1 [-1038.9, -367.2]) |
| Broken crew connections | 44.5 [33.9, 54.0] | 33.1 (-11.4 [-15.8, -6.7]) | 32.5 (+1.0 [-3.0, +5.0]) | 39.1 (-5.4 [-7.5, -3.1]) | 29.7 (-14.8 [-19.7, -9.7]) |
| Crews replaced at duty limit | 8.5 [5.2, 12.5] | 9.7 (+1.2 [+0.1, +2.4]) | 9.0 (-1.0 [-2.0, +0.0]) | 7.2 (-1.3 [-2.3, -0.4]) | 8.4 (-0.1 [-1.3, +1.3]) |
| Duty overruns in the air | 3.2 [1.5, 5.2] | 3.1 (-0.1 [-1.0, +0.6]) | 4.0 (-0.5 [-1.0, +0.0]) | 3.1 (-0.1 [-0.3, +0.0]) | 3.0 (-0.2 [-1.0, +0.5]) |

Days: 10; hard feasible on 2 (20%).

</details>

**Does robust planning add anything once repair exists?** Best soft + repair minus baseline + repair, paired by day (crew-caused delay, min/day; broken connections/day):

| Window | Days | Δ delay | Δ broken connections | Δ crews used |
|---|---|---|---|---|
| test summer | 62 | -98.5 [-151.4, -54.7] | -6.2 [-7.0, -5.3] | +2.9 [+2.5, +3.4] |
| test winter | 61 | -47.6 [-76.3, -25.1] | -5.2 [-6.2, -4.2] | +2.2 [+1.7, +2.7] |
| test summer (worst-weather days) | 10 | -54.7 [-135.7, +12.7] | -6.6 [-9.7, -3.9] | +3.2 [+1.9, +4.5] |
| test winter (worst-weather days) | 10 | -177.9 [-314.1, -67.6] | -9.4 [-13.0, -5.9] | +3.2 [+2.1, +4.3] |
<!-- RESULTS:END -->

**Reading the results.** All numbers above are generated; this paragraph only says how to read them. Robust planning on its own cuts broken connections and crew-caused delay; the λ chosen on calibration days buys this with a few more crews, within the +2% pay-hour cap set before testing. Day-of repair (swapping crews and calling reserves) is the bigger lever: it removes most crew-caused delay whatever the plan. Whether robust planning still adds to repair is answered by the paired table at the end of the details: it keeps cutting broken connections, and a smaller amount of delay on top of what repair removes. A crew that would run past its duty limit is replaced rather than waited for, so those replacements are counted as their own row; the robust plan does not reduce them and in winter has slightly more, a cost the delay totals do not show. The hard-constraint planner does not work well here, and the operations-feature machinery (morning model and re-solve) adds little (see limitations).

## Two-day pairings (Phase 9)

One-day pairings must leave and return to CLT on the same duty day, so evening departures to a spoke and early-morning arrivals from one can never be planned. `multiday.py` lets a crew fly out to a spoke, rest there (Part 117's 10 hours, i.e. at least 11.5 h from block-in to block-out with report and release), and fly back the next day. Days are planned in non-overlapping two-day blocks so every planner still covers exactly the same flights; pairings do not cross a block boundary.

Joining every outbound duty with every compatible return gives 1-4 million pairings per block, too many to solve. Cost and risk are sums over duties, so the solver picks duties instead (about 20-40 thousand per block) and per-spoke counting constraints guarantee the chosen outbound and return duties can be paired with legal rest; a first-in-first-out matching then rebuilds the pairings (tested, as is the duty enumeration against brute force). Each overnight costs 3 pay hours (hotel and per diem, a convention). Connections that only multi-day duties use are scored by the same trained planning model, without refitting. The planner ignores the risk of a rest being cut short; the replay enforces the rest and counts it. Day-of repair is not modelled for two-day pairings, so these are no-repair replays.

<!-- MULTIDAY:START -->
Blocks of 2 days; λ chosen on calibration blocks: **2** (largest CALIB delay reduction with cost <= +2%). 453 of 455 soft-robust solves are proven optimal; the cheapest plan's cost-tie plateau usually is not, so it comes from CP-SAT's deterministic search: its cost is on average 14 and at most 156 minutes of pay credit above the solver's proven lower bound per block (508 of 546 solves proven optimal overall). Crews and duties count pairings and crew-days; overnights are identical across planners because the plannable set fixes which flights need one.

| Window | Blocks | Plannable flights, one-day | Plannable flights, two-day | Extra flights planned per block | Overnights per block (baseline) |
|---|---|---|---|---|---|
| calib | 30 | 56.9% | 67.2% | 135 | 110 |
| test summer | 31 | 56.9% | 67.1% | 135 | 112 |
| test winter | 30 | 55.0% | 66.7% | 147 | 109 |

**test summer**, per 2-day block, cheapest vs soft-robust (paired Δ, 95% bootstrap over blocks):

| Metric | Cheapest | Soft robust | Δ |
|---|---|---|---|
| Crews (pairings) | 172.3 | 186.2 | +13.8 [+12.8, +14.7] |
| Crew duties | 284.3 | 298.2 | +13.8 [+12.8, +14.7] |
| Overnights | 112.0 | 112.0 | +0.0 [+0.0, +0.0] |
| Cost (pay hours) | 2973.5 | 3029.4 | +55.9 [+51.7, +59.1] |
| Crew-caused delay (min) | 2199.3 | 1507.6 | -691.7 [-1009.2, -379.6] |
| Broken crew connections | 113.3 | 81.8 | -31.4 [-35.4, -27.5] |
| Rest cut short | 11.3 | 11.4 | +0.2 [-0.3, +0.6] |
| Crews replaced at duty limit | 41.4 | 34.1 | -7.3 [-9.2, -5.5] |
| Duty overruns in the air | 8.3 | 7.3 | -1.0 [-2.0, +0.0] |

**test winter**, per 2-day block, cheapest vs soft-robust (paired Δ, 95% bootstrap over blocks):

| Metric | Cheapest | Soft robust | Δ |
|---|---|---|---|
| Crews (pairings) | 172.1 | 180.5 | +8.4 [+7.3, +9.3] |
| Crew duties | 281.4 | 289.8 | +8.4 [+7.3, +9.3] |
| Overnights | 109.3 | 109.3 | +0.0 [+0.0, +0.0] |
| Cost (pay hours) | 2937.0 | 2970.9 | +33.9 [+29.9, +37.5] |
| Crew-caused delay (min) | 1251.5 | 659.0 | -592.6 [-784.0, -419.4] |
| Broken crew connections | 70.0 | 43.1 | -26.9 [-31.7, -22.7] |
| Rest cut short | 5.4 | 5.5 | +0.1 [-0.5, +0.6] |
| Crews replaced at duty limit | 16.4 | 15.3 | -1.1 [-2.7, +0.4] |
| Duty overruns in the air | 4.8 | 4.9 | +0.1 [-0.7, +0.9] |
<!-- MULTIDAY:END -->

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
6. **Replay** (`simulate.py`). Re-flies the whole AA network each day: a flight leaves at max(scheduled + own delay, aircraft ready, crew ready). The real airline's own aircraft propagation is removed from each delay (`DepDelay − LateAircraftDelay`) and re-created by the simulator, so it is not counted twice. A crew waits for a late inbound only while it stays legal; if flying the next leg would take it past its duty limit, it is replaced (counted) and the rest of its day goes to replacement crews, so those legs wait only for their aircraft. Crew-caused delay is measured against a no-crew counterfactual, so knock-on delay through aircraft counts.
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

Styled after [DrugSupply Watch](https://anoopibrampur.github.io/drugsupply-watch/) (Newsreader and Public Sans, paper-and-ink palette, light and dark). Pick a season, a day (worst first) and whether day-of repair runs; the robustness weight λ sits under Settings. The app replays that day live, compares the robust plan with the cheapest plan in a row of key numbers and a short plain-language summary, and shows each crew's day as a timeline: grey is the schedule, colour the replay, by crew-caused delay including knock-on through the aircraft. A list shows every flight that waited for a late crew. Run it from the repository root so `.streamlit/config.toml` (theme and fonts) applies. It uses the full pipeline database when present, otherwise `app/demo.duckdb`, a 14 MB committed extract of the test days (built by `python -m robust_crew.demo_extract`), so it runs straight after cloning.

**Hosting it on Streamlit Community Cloud** (free): at [share.streamlit.io](https://share.streamlit.io) choose *Create app*, point it at this repository, branch `main`, main file `app/streamlit_app.py`, and pick Python 3.11 under advanced settings. It installs `app/requirements.txt` and runs on the extract.

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

- **Synthetic crews, simplified rules.** No rest, cumulative or contractual rules; no deadheads; reserves are idealised airport standby at the hub. In the replay a crew that would pass its Table B duty limit is replaced (counted), never extended; replacements are assumed available.
- **One-day pairings in the main results; two-day pairings in Phase 9 only.** The headline experiments use one-day pairings, so evening departures and early-morning arrivals at CLT cannot be planned (per-day shares in `reports/phase3_pairings.json`). Phase 9 adds two-day pairings with an outstation overnight, in non-overlapping two-day blocks: flights at block edges are still unplannable, there is no day-of repair for them, pairings are at most two days, and the rest rule is Part 117's 10 hours only.
- **Actual, not planned, aircraft.** BTS `Tail_Number` is the aircraft that flew, including day-of swaps. Most of the break model's lift over simple baselines comes from aircraft-rotation features, so it leans on actual tails ≈ planned tails (`python -m robust_crew.ablation_tail` reports the model without them).
- **Probabilities drift with the season.** Calibrated on May-June, the model under-predicts breaks in the stormy summer and over-predicts in the calm winter; ranking holds up.
- **The hard-robust planner mostly fails.** Forbidding risky connections outright leaves no exact cover on most days, even after exempting legs that have no low-risk option; it is evaluated only on its feasible days, where it gains little. Soft penalties are the better tool here.
- **No passenger connections, no cancellations decisions, one hub.** Delay is measured in flight minutes, not passenger minutes; cancelled flights are taken as given.
- **Crews wait while they are legal.** A crew holds its next flight for a late inbound only until flying that flight would take it past its duty limit (judged on scheduled block time); then it is replaced, and that flight and the rest of the crew's day go to replacement crews assumed ready on time (not drawn from the reserve pool, duty not checked). Before this rule, a crew behind a real delay of a day or more held its next flight just as long, which inflated some days' crew-caused delay. A legal but very long wait (several hours) is still counted in full; a real airline might swap crews sooner.
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
