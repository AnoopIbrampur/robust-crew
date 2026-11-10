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
