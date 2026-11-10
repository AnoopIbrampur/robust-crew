"""Final results: trade-off curves, the comparison table, worst-weather days.

Reads the stored replay (Phase 6) and repair (Phase 7) results and writes
everything the README quotes, so no number is typed by hand:

- reports/phase8_results.json        every number below
- reports/results.md                 tables (also spliced into README.md)
- reports/figures/tradeoff.png       crews vs crew-caused delay, per test window
- reports/figures/worst_days.png     the same on the worst-weather days

Selection rules, fixed before looking at test results and applied to CALIB
days only:

- best soft lambda: the largest mean crew-delay reduction vs baseline among
  lambdas whose mean cost is at most ``MAX_COST_INCREASE`` above baseline.
- best hard theta: the largest paired (same-day) mean crew-delay reduction among
  thetas feasible on at least ``MIN_HARD_FEASIBLE`` of CALIB days.

Hard-robust results are always paired: compared with the baseline on the days
the hard planner was feasible, and the share of feasible days is reported.

Worst-weather days: the ``N_WORST`` days of each test window with the highest
mean actual departure delay at the hub across all carriers (an outcome
external to every planner).

Usage::

    uv run python -m robust_crew.experiments
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from robust_crew import config
from robust_crew.ingest import connect

MAX_COST_INCREASE = 0.02
MIN_HARD_FEASIBLE = 0.25
N_WORST = 10
N_BOOT = 10_000
SEED = 0
METRICS = ["crews_used", "cost", "crew_delay_min", "broken_connections", "duty_violations"]
LABELS = {
    "crews_used": "Crews used", "cost": "Cost (pay hours)", "crew_delay_min": "Crew-caused delay (min)",
    "broken_connections": "Broken crew connections", "duty_violations": "Duty-limit violations",
}


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Repair results (all modes), plan log, and per-day hub mean departure delay."""
    con = connect()
    rep = con.execute("SELECT * FROM repair_results").df()
    plan = con.execute("SELECT date, split, planner, param, status FROM plan_log").df()
    hub = con.execute(
        "SELECT FlightDate, avg(DepDelay) AS d FROM flights WHERE Origin = ? GROUP BY 1", [config.HUB]
    ).df()
    con.close()
    for df in (rep, plan):
        df["date"] = pd.to_datetime(df["date"]).dt.date
    hub_delay = hub.set_index(pd.to_datetime(hub.FlightDate).dt.date)["d"]
    rep["param"] = rep["param"].astype(float)
    return rep, plan, hub_delay


def variant(df: pd.DataFrame, planner: str, param: float | None, repair: str) -> pd.DataFrame:
    """Rows for one planner variant and repair mode, indexed by date."""
    m = (df.planner == planner) & (df.repair == repair)
    m &= df.param.isna() if param is None else np.isclose(df.param, param)
    return df[m].set_index("date").sort_index()


def boot_ci(x: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """95% percentile bootstrap interval of the mean (resampling days)."""
    if len(x) < 2:
        return (float("nan"), float("nan"))
    idx = rng.integers(0, len(x), size=(N_BOOT, len(x)))
    means = x[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def describe(a: pd.DataFrame, base: pd.DataFrame, rng: np.random.Generator) -> dict:
    """Mean and CI per metric for ``a``, plus the paired difference vs ``base`` on shared days."""
    days = a.index.intersection(base.index)
    out = {"days": int(len(days))}
    for m in METRICS:
        x = a.loc[days, m].to_numpy(float)
        d = x - base.loc[days, m].to_numpy(float)
        lo, hi = boot_ci(x, rng)
        dlo, dhi = boot_ci(d, rng)
        out[m] = {"mean": round(float(x.mean()), 2), "ci": [round(lo, 2), round(hi, 2)],
                  "diff_vs_baseline": round(float(d.mean()), 2), "diff_ci": [round(dlo, 2), round(dhi, 2)]}
    return out


def choose_params(rep: pd.DataFrame, plan: pd.DataFrame) -> dict:
    """Pick best soft lambda and hard theta on CALIB days (no repair), per the module rules."""
    cal = rep[(rep.split == "calib") & (rep.repair == "none")]
    base = variant(cal, "baseline", None, "none")
    soft = []
    for lam in sorted(cal[cal.planner == "soft"].param.unique()):
        s = variant(cal, "soft", lam, "none")
        soft.append({"lambda": float(lam),
                     "cost_increase": float(s.cost.mean() / base.cost.mean() - 1),
                     "delay_reduction": float(base.crew_delay_min.mean() - s.crew_delay_min.mean())})
    ok = [s for s in soft if s["cost_increase"] <= MAX_COST_INCREASE]
    best_soft = max(ok, key=lambda s: s["delay_reduction"])["lambda"]

    hard = []
    n_cal = int(((plan.split == "calib") & (plan.planner == "baseline")).sum())
    for th in sorted(cal[cal.planner == "hard"].param.unique()):
        h = variant(cal, "hard", th, "none")
        if len(h) / n_cal < MIN_HARD_FEASIBLE:
            continue
        days = h.index
        hard.append({"theta": float(th), "feasible_share": len(h) / n_cal,
                     "paired_delay_reduction": float(base.loc[days].crew_delay_min.mean() - h.crew_delay_min.mean())})
    best_hard = max(hard, key=lambda h: h["paired_delay_reduction"])["theta"] if hard else None
    return {"best_soft_lambda": best_soft, "best_hard_theta": best_hard,
            "soft_candidates": soft, "hard_candidates": hard}


def tradeoff_points(rep: pd.DataFrame, split: str, repair: str, days=None) -> list[dict]:
    """(crews, delay) per planner variant for one split and repair mode."""
    g = rep[(rep.split == split) & (rep.repair == repair)]
    if days is not None:
        g = g[g.date.isin(days)]
    pts = []
    for (planner, param), h in g.groupby(["planner", "param"], dropna=False):
        pts.append({"planner": planner, "param": None if pd.isna(param) else float(param),
                    "days": int(len(h)), "crews": float(h.crews_used.mean()),
                    "cost": float(h.cost.mean()), "delay": float(h.crew_delay_min.mean()),
                    "broken": float(h.broken_connections.mean())})
    return pts


def plot_tradeoff(points: dict, path, title: str) -> None:
    """Crews used vs crew-caused delay; soft lambda curve, baseline marked, repair overlaid."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    splits = list(points)
    fig, axes = plt.subplots(1, len(splits), figsize=(6 * len(splits), 4.6))
    for ax, split in zip(np.atleast_1d(axes), splits):
        for repair, color in [("none", "tab:blue"), ("reactive", "tab:orange")]:
            pts = points[split][repair]
            soft = sorted([p for p in pts if p["planner"] == "soft"], key=lambda p: p["param"])
            base = [p for p in pts if p["planner"] == "baseline"][0]
            xs = [base["crews"]] + [p["crews"] for p in soft]
            ys = [base["delay"]] + [p["delay"] for p in soft]
            lab = "no repair" if repair == "none" else "with reactive repair"
            ax.plot(xs, ys, "-o", color=color, ms=4, label=f"soft robust, {lab}")
            ax.plot(base["crews"], base["delay"], "*", color=color, ms=16, mec="black",
                    label=f"baseline, {lab}")
            for p in soft:
                if p["param"] in (0.5, 4, 16, 64):
                    ax.annotate(f"λ={p['param']:g}", (p["crews"], p["delay"]), fontsize=7,
                                xytext=(4, 4), textcoords="offset points", color=color)
        ax.set_title(f"{split.replace('_', ' ')} ({base['days']} days)")
        ax.set_xlabel("crews used per day (incl. reserves called)")
        ax.set_ylabel("crew-caused propagated delay (min/day)")
        ax.grid(alpha=0.3)
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=7)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fmt(cell: dict, signed: bool = False) -> str:
    """'mean [lo, hi]' for the table."""
    if signed:
        return f"{cell['diff_vs_baseline']:+.1f} [{cell['diff_ci'][0]:+.1f}, {cell['diff_ci'][1]:+.1f}]"
    return f"{cell['mean']:.1f} [{cell['ci'][0]:.1f}, {cell['ci'][1]:.1f}]"


def headline(res: dict) -> list[str]:
    """One bullet per test window with the key generated numbers."""
    out = []
    for split, rows in res["all_days"].items():
        b, s = rows["baseline"], rows["soft"]
        br, sr = rows["baseline_repair"], rows["soft_repair"]
        d = rows["soft_repair_vs_baseline_repair"]
        pct = lambda x, y: 100 * (x - y) / y  # noqa: E731
        out.append(
            f"- **{split.replace('_', ' ')}** ({b['days']} days): with no repair, the selected soft-robust plan cuts "
            f"crew-caused delay from {b['crew_delay_min']['mean']:.0f} to {s['crew_delay_min']['mean']:.0f} min/day "
            f"({pct(s['crew_delay_min']['mean'], b['crew_delay_min']['mean']):+.0f}%; paired Δ "
            f"{s['crew_delay_min']['diff_vs_baseline']:+.0f} [{s['crew_delay_min']['diff_ci'][0]:+.0f}, "
            f"{s['crew_delay_min']['diff_ci'][1]:+.0f}]"
            f"{', interval includes zero' if s['crew_delay_min']['diff_ci'][0] <= 0 <= s['crew_delay_min']['diff_ci'][1] else ''}"
            f") and broken connections from "
            f"{b['broken_connections']['mean']:.1f} to {s['broken_connections']['mean']:.1f} "
            f"for {s['crews_used']['diff_vs_baseline']:+.1f} crews/day. Reactive repair alone takes the baseline to "
            f"{br['crew_delay_min']['mean']:.0f} min/day; robust planning plus repair reaches "
            f"{sr['crew_delay_min']['mean']:.0f} (paired Δ vs repair alone {d['crew_delay_min']['diff_vs_baseline']:+.0f} "
            f"[{d['crew_delay_min']['diff_ci'][0]:+.0f}, {d['crew_delay_min']['diff_ci'][1]:+.0f}] min/day)."
        )
    return out


def extra_findings(res: dict) -> list[str]:
    """Generated sentences for secondary findings and robustness checks."""
    out = []
    ops = res.get("ops_model_auc_gain", {})
    if ops:
        gains = ", ".join(f"{k.replace('_', ' ')} {v:+.3f}" for k, v in ops.items())
        out.append(f"- **Operations features barely help.** The morning-of model's AUC gain over the planning model: {gains}.")
    for split, r in res.get("morning_resolve_effect", {}).items():
        out.append(
            f"- **Morning re-solve ({split.replace('_', ' ')})**, the one repair step that uses operations "
            f"probabilities: Δ crew-caused delay vs no re-solve {fmt(r['baseline'], True)} min/day on the baseline plan "
            f"and {fmt(r['soft'], True)} on the selected soft plan (each without reactive repair). "
            "Reactive repair, which drives the headline effect, does not use the model's probabilities.")
    for split, r in res.get("excluding_thanksgiving", {}).items():
        out.append(
            f"- **Excluding Thanksgiving ({split.replace('_', ' ')}, {r['days']} days):** soft vs baseline Δ delay "
            f"{fmt(r['soft'], True)}; soft + repair vs baseline + repair {fmt(r['soft_repair'], True)} min/day.")
    tb = res.get("tiebreak")
    if tb:
        for split, w in tb["windows"].items():
            out.append(
                f"- **Baseline tie-breaking ({split.replace('_', ' ')}):** re-solving the baseline with {tb['n_seeds']} "
                f"different choices among cost-tied optima gives mean crew-caused delay between {w['min']:.0f} and "
                f"{w['max']:.0f} min/day (largest cost difference {w['max_cost_diff_pay_hours']:.2f} pay hours, "
                f"i.e. equally cheap). The selected soft plan's {res['all_days'][split]['soft']['crew_delay_min']['mean']:.0f} "
                + ("beats every one of them, so its gain is more than lucky tie-breaking; but part of the cheap "
                   "low-λ gain is simply choosing well among schedules that cost the same."
                   if res['all_days'][split]['soft']['crew_delay_min']['mean'] < w['min'] else
                   "falls inside that range: the low-λ gain is mostly choosing among equally cheap schedules."))
    return out


def results_markdown(res: dict) -> str:
    """Markdown tables of the main comparison for each test window."""
    sel = res["selection"]
    lines = headline(res) + [""] + extra_findings(res) + ["", "![Trade-off curve](reports/figures/tradeoff.png)", "",
                             "![Worst-weather days](reports/figures/worst_days.png)", "",
                             "<details><summary>Full tables with confidence intervals</summary>", ""]
    lines += [
        f"Selected on CALIB days: soft λ = {sel['best_soft_lambda']:g} "
        f"(largest delay reduction with cost ≤ +{MAX_COST_INCREASE:.0%}); "
        + (f"hard θ = {sel['best_hard_theta']:g}." if sel["best_hard_theta"] is not None else "no hard θ qualified."),
        "",
        "Means per day with 95% bootstrap intervals over days in brackets; (Δ ...) is the paired difference vs "
        "the no-repair baseline on the same days, with its 95% interval. Hard robust is evaluated only on days "
        "it was feasible (its mean is over those days). Repair = reactive repair (idle-crew swaps + up to "
        "4 reserves); reserves called count as crews used, but the cost column excludes reserve pay (reserves are "
        "on standby payroll either way). Intervals over very few days are unreliable.",
        "",
    ]
    for block, title in [("all_days", "All test days"), ("worst_days", f"{N_WORST} worst-weather days")]:
        for split, rows in res[block].items():
            lines += [f"**{title}, {split.replace('_', ' ')}**", "",
                      "| Metric | Baseline | Best soft (Δ) | Best hard, paired days (Δ) | Baseline + repair (Δ) | Best soft + repair (Δ) |",
                      "|---|---|---|---|---|---|"]
            for m in METRICS:
                b = rows["baseline"][m]
                cell = lambda k: f"{rows[k][m]['mean']:.1f} ({rows[k][m]['diff_vs_baseline']:+.1f} [{rows[k][m]['diff_ci'][0]:+.1f}, {rows[k][m]['diff_ci'][1]:+.1f}])"  # noqa: E731
                h = cell("hard") if rows.get("hard") else "n/a"
                lines.append(f"| {LABELS[m]} | {fmt(b)} | {cell('soft')} | {h} | {cell('baseline_repair')} | {cell('soft_repair')} |")
            hd = rows.get("hard")
            lines += ["", f"Days: {rows['baseline']['days']}"
                      + (f"; hard feasible on {hd['days']} ({rows['hard_feasible_share']:.0%})." if hd else ".") , ""]
    lines += ["</details>", ""]
    lines += ["**Does robust planning add anything once repair exists?** Best soft + repair minus baseline + repair, "
              "paired by day (crew-caused delay, min/day; broken connections/day):", "",
              "| Window | Days | Δ delay | Δ broken connections | Δ crews used |", "|---|---|---|---|---|"]
    for block in ["all_days", "worst_days"]:
        for split, rows in res[block].items():
            r = rows["soft_repair_vs_baseline_repair"]
            name = split.replace("_", " ") + (" (worst-weather days)" if block == "worst_days" else "")
            lines.append(f"| {name} | {r['days']} | {fmt(r['crew_delay_min'], True)} | "
                         f"{fmt(r['broken_connections'], True)} | {fmt(r['crews_used'], True)} |")
    return "\n".join(lines)


def main() -> None:
    """Compute everything and write reports, figures and the README results block."""
    rng = np.random.default_rng(SEED)
    rep, plan, hub_delay = load()
    sel = choose_params(rep, plan)
    lam, th = sel["best_soft_lambda"], sel["best_hard_theta"]
    res: dict = {"selection": sel, "all_days": {}, "worst_days": {}, "worst_day_dates": {},
                 "repair_effect": {}, "tradeoff": {}}

    for split in config.TEST_WINDOWS:
        g = rep[rep.split == split]
        days_all = sorted(g.date.unique())
        worst = sorted(sorted(days_all, key=lambda d: -hub_delay.get(d, 0))[:N_WORST])
        res["worst_day_dates"][split] = [str(d) for d in worst]
        for block, days in [("all_days", days_all), ("worst_days", worst)]:
            gg = g[g.date.isin(days)]
            base = variant(gg, "baseline", None, "none")
            rows = {
                "baseline": describe(base, base, rng),
                "soft": describe(variant(gg, "soft", lam, "none"), base, rng),
                "soft_repair": describe(variant(gg, "soft", lam, "reactive"), base, rng),
                "baseline_repair": describe(variant(gg, "baseline", None, "reactive"), base, rng),
            }
            # Does robust planning still help once repair exists? Paired vs baseline + repair.
            brep = variant(gg, "baseline", None, "reactive")
            rows["soft_repair_vs_baseline_repair"] = describe(variant(gg, "soft", lam, "reactive"), brep, rng)
            if th is not None:
                h = variant(gg, "hard", th, "none")
                if len(h):
                    rows["hard"] = describe(h, base, rng)
                    rows["hard_feasible_share"] = len(h) / len(days)
            res[block][split] = rows
        res["tradeoff"][split] = {r: tradeoff_points(rep, split, r) for r in ["none", "reactive"]}
        res["tradeoff"][split + "_worst"] = {r: tradeoff_points(rep, split, r, worst) for r in ["none", "reactive"]}
        # Repair modes for baseline and best soft (all days).
        modes = {}
        base = variant(g, "baseline", None, "none")
        for planner, param in [("baseline", None), ("soft", lam)]:
            for mode in ["none", "reactive", "morning", "morning+reactive"]:
                v = variant(g, planner, param, mode)
                d = describe(v, base, rng)
                modes[f"{planner}|{mode}"] = {
                    **{m: d[m] for m in ["crew_delay_min", "crews_used", "broken_connections", "duty_violations"]},
                    "swaps": round(float(v.swaps.mean()), 2), "reserves_used": round(float(v.reserves_used.mean()), 2),
                }
        res["repair_effect"][split] = modes

    # Operations-model AUC gain (Phase 4) and the morning re-solve effect (Phase 7).
    p4 = json.loads((config.REPORTS_DIR / "phase4_delay_model.json").read_text())["metrics"]
    res["ops_model_auc_gain"] = {
        s: round(p4[s]["operations_model"]["auc"] - p4[s]["planning_model"]["auc"], 3) for s in config.TEST_WINDOWS}
    res["morning_resolve_effect"] = {}
    res["excluding_thanksgiving"] = {}
    for split in config.TEST_WINDOWS:
        g = rep[rep.split == split]
        res["morning_resolve_effect"][split] = {
            "baseline": describe(variant(g, "baseline", None, "morning"), variant(g, "baseline", None, "none"), rng)["crew_delay_min"],
            "soft": describe(variant(g, "soft", lam, "morning"), variant(g, "soft", lam, "none"), rng)["crew_delay_min"],
        }
        hol = [d for d in g.date.unique() if d.month == 11 and 22 <= d.day <= 28 and d.weekday() == 3]
        if hol:
            gg = g[~g.date.isin(hol)]
            base, brep = variant(gg, "baseline", None, "none"), variant(gg, "baseline", None, "reactive")
            res["excluding_thanksgiving"][split] = {
                "excluded": [str(d) for d in hol], "days": int(gg.date.nunique()),
                "soft": describe(variant(gg, "soft", lam, "none"), base, rng)["crew_delay_min"],
                "soft_repair": describe(variant(gg, "soft", lam, "reactive"), brep, rng)["crew_delay_min"],
            }
    tb_path = config.REPORTS_DIR / "tiebreak_sensitivity.json"
    if tb_path.exists():
        res["tiebreak"] = json.loads(tb_path.read_text())

    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    plot_tradeoff({s: res["tradeoff"][s] for s in config.TEST_WINDOWS},
                  config.FIGURES_DIR / "tradeoff.png",
                  "Crews vs crew-caused delay, replaying real delays (soft-robust λ sweep)")
    plot_tradeoff({s: res["tradeoff"][s + "_worst"] for s in config.TEST_WINDOWS},
                  config.FIGURES_DIR / "worst_days.png",
                  f"The {N_WORST} worst-weather days of each test window")

    (config.REPORTS_DIR / "phase8_results.json").write_text(json.dumps(res, indent=2, default=str))
    md = results_markdown(res)
    (config.REPORTS_DIR / "results.md").write_text(md + "\n")
    readme = config.ROOT / "README.md"
    text = readme.read_text()
    block = f"<!-- RESULTS:START -->\n{md}\n<!-- RESULTS:END -->"
    if "<!-- RESULTS:START -->" in text:
        text = re.sub(r"<!-- RESULTS:START -->.*<!-- RESULTS:END -->", lambda _: block, text, flags=re.S)
        readme.write_text(text)
    print(md)


if __name__ == "__main__":
    main()
