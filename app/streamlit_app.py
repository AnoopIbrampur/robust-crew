"""robust-crew explorer: baseline vs robust crew schedules on a real test day.

Run after the pipeline has built data/robust_crew.duckdb:

    uv sync --extra app
    uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from robust_crew import config
from robust_crew.repair import replay_with_repair
from robust_crew.simulate import crew_caused_delay, load_day_flights, replay

ET = ZoneInfo("America/New_York")

st.set_page_config(page_title="robust-crew explorer", layout="wide")


@st.cache_resource
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    return con


@st.cache_data
def test_days() -> pd.DataFrame:
    """Test days with the baseline's crew-caused delay (no repair), for the picker."""
    return db().execute(
        """SELECT date, split, crew_delay_min FROM replay_results
           WHERE planner = 'baseline' AND split LIKE 'test%' ORDER BY date"""
    ).df()


@st.cache_data
def soft_lambdas() -> list[float]:
    rows = db().execute("SELECT DISTINCT param FROM plan_log WHERE planner = 'soft' ORDER BY 1").fetchall()
    return [float(r[0]) for r in rows]


def selected_lambda() -> float | None:
    path = config.REPORTS_DIR / "phase8_results.json"
    if path.exists():
        return json.loads(path.read_text())["selection"]["best_soft_lambda"]
    return None


@st.cache_data
def pairings_for(day: date, planner: str, param: float | None) -> list[list[str]]:
    q = "SELECT crew, flight_id FROM schedules WHERE date = ? AND planner = ? AND "
    q += "param IS NULL" if param is None else "param = ?"
    args = [day, planner] + ([] if param is None else [param])
    df = db().execute(q + " ORDER BY crew, pos", args).df()
    return [list(g.flight_id) for _, g in df.groupby("crew", sort=True)]


@st.cache_data
def flights_for(day: date):
    return load_day_flights(db(), day)


def run(day: date, pairings: list[list[str]], repair: bool):
    """Replay one schedule; return per-leg rows and summary metrics."""
    flights = flights_for(day)
    by_id = {f.flight_id: f for f in flights}
    base = replay(flights, None)
    if repair:
        res = replay_with_repair(flights, pairings)
        crews = res.crews_flown
        extra = {"Swaps": res.swaps, "Reserves": res.reserves_used}
    else:
        res = replay(flights, pairings)
        crews = [[x for x in p if by_id[x].completed] for p in pairings]
        extra = {}
    rows = []
    for c, legs in enumerate(crews):
        for pos, fid in enumerate(legs):
            f = by_id[fid]
            arr = res.sim_arr[fid]
            dep = arr - pd.Timedelta(minutes=f.actual_block)
            rows.append({
                "crew": c, "pos": pos, "flight": fid.split("_")[2], "flight_id": fid,
                "route": f"{f.origin}-{f.dest}", "tail": f.tail or "?",
                "sched_dep": f.sched_dep.astimezone(ET), "sched_arr": f.sched_arr.astimezone(ET),
                "sim_dep": dep.astimezone(ET), "sim_arr": arr.astimezone(ET),
                "own_delay": round(f.own_dep_delay),
                "crew_wait": round(res.crew_delay_at.get(fid, 0.0)),
                "crew_caused": round((arr - base.sim_arr[fid]).total_seconds() / 60),
            })
    legs = pd.DataFrame(rows)
    if len(legs):
        first = legs.groupby("crew").sched_dep.min().sort_values()
        order = {c: i + 1 for i, c in enumerate(first.index)}
        legs["crew_row"] = legs.crew.map(order)
    metrics = {
        "Crews": len(crews),
        "Crew delay (min)": round(crew_caused_delay(res, base)),
        "Broken conn.": res.broken_connections,
        "Duty violations": res.duty_violations,
        **extra,
    }
    return legs, metrics


def gantt(legs: pd.DataFrame, only_affected: bool, title: str, day: date) -> alt.Chart:
    """Crew-by-crew timeline: grey = scheduled, coloured = replayed with real delays.

    The time axis shows the operating day (04:00 to 08:00 next day, Eastern);
    legs replayed later than that are clipped and counted in the title.
    """
    d = legs
    if only_affected:
        hit = d.groupby("crew_row").crew_caused.max()
        d = d[d.crew_row.isin(hit[hit > 0].index)]
    if d.empty:
        return alt.Chart(pd.DataFrame({"t": ["No crew-caused delay on this day."]})).mark_text(size=14).encode(text="t")
    d = d.assign(impact=pd.cut(d.crew_caused, [-1, 0, 15, 60, 10_000],
                               labels=["none", "1-15 min", "16-60 min", "> 60 min"]))
    lo = pd.Timestamp(datetime(day.year, day.month, day.day, 4), tz=ET)
    hi = lo + pd.Timedelta(hours=28)
    clipped = int((d.sim_arr > hi).sum())
    if clipped:
        title = f"{title}  ({clipped} legs end after 08:00 next day, clipped)"
    xs = alt.Scale(domain=[lo.isoformat(), hi.isoformat()])
    y = alt.Y("crew_row:O", title="crew (by report time)", sort="ascending", axis=alt.Axis(labelFontSize=9))
    tip = ["flight", "route", "tail", alt.Tooltip("sched_dep:T", format="%H:%M"),
           alt.Tooltip("sim_dep:T", format="%H:%M"), "own_delay", "crew_wait", "crew_caused"]
    sched = alt.Chart(d).mark_bar(color="#cfcfcf", height=5, clip=True).encode(
        x=alt.X("sched_dep:T", title="time (Eastern)", scale=xs,
                axis=alt.Axis(format="%H:%M", tickCount=14, labelAngle=0)),
        x2="sched_arr:T", y=y, tooltip=tip)
    sim = alt.Chart(d).mark_bar(height=9, opacity=0.85, clip=True).encode(
        x=alt.X("sim_dep:T", scale=xs), x2="sim_arr:T", y=y, tooltip=tip,
        color=alt.Color("impact:N", title="crew-caused delay", legend=alt.Legend(orient="top"),
                        scale=alt.Scale(domain=["none", "1-15 min", "16-60 min", "> 60 min"],
                                        range=["#4c78a8", "#f2cf5b", "#f58518", "#d62728"])))
    return (sched + sim).properties(title=title, height=alt.Step(14))


# --- Page ------------------------------------------------------------------------

st.title("robust-crew explorer")
st.caption(
    "American Airlines crews based at Charlotte (CLT), one-day pairings, replayed against the delays that "
    "actually happened. Crews are synthetic and rules simplified (Part 117 tables only): not a compliance claim."
)

if not config.DB_PATH.exists():
    st.error("No database found. Run `uv run python -m robust_crew.pipeline` first.")
    st.stop()

days = test_days()
with st.sidebar:
    window = st.radio("Test window", ["test_summer", "test_winter"],
                      format_func=lambda s: s.replace("test_", "").title() + " 2023")
    wd = days[days.split == window].sort_values("crew_delay_min", ascending=False)
    labels = {pd.Timestamp(r.date).date(): f"{pd.Timestamp(r.date):%a %b %d}  ·  baseline {r.crew_delay_min:,.0f} min"
              for r in wd.itertuples()}
    day = st.selectbox("Day (sorted by baseline crew-caused delay)", list(labels), format_func=labels.get)
    lams = soft_lambdas()
    best = selected_lambda()
    lam = st.select_slider("Robust plan: soft λ (pay hours per expected disrupted leg)", lams,
                           value=best if best in lams else lams[0])
    if best is not None:
        st.caption(f"λ = {best:g} was selected on calibration days.")
    repair = st.toggle("Apply day-of repair (swaps + reserves)", value=False)
    only_affected = st.toggle("Show only crews hit by crew-caused delay", value=True)

base_legs, base_m = run(day, pairings_for(day, "baseline", None), repair)
soft_legs, soft_m = run(day, pairings_for(day, "soft", lam), repair)

st.subheader(f"{day:%A %B %d, %Y}" + ("  ·  with repair" if repair else ""))
cols = st.columns(len(base_m))
for col, (k, v) in zip(cols, base_m.items()):
    sv = soft_m[k]
    col.metric(k, f"{sv:,}", delta=f"{sv - v:+,} (baseline {v:,})", delta_color="inverse")
st.caption("Big numbers are the robust plan; deltas compare with the cost-minimizing baseline on the same day.")

tab_b, tab_s = st.tabs(["Baseline (minimum cost)", f"Soft robust, λ = {lam:g}"])
tab_b.altair_chart(gantt(base_legs, only_affected, "Baseline (minimum cost)", day), width="stretch")
tab_s.altair_chart(gantt(soft_legs, only_affected, f"Soft robust, λ = {lam:g}", day), width="stretch")
st.caption(
    "Grey: scheduled leg. Coloured: the leg as replayed with real delays, coloured by crew-caused delay "
    "(arrival vs a replay with no crew constraints, so knock-on delay through the aircraft counts)."
)

with st.expander("Where delay propagated: legs held by their crew"):
    for name, legs in [("Baseline", base_legs), (f"Soft λ = {lam:g}", soft_legs)]:
        held = legs[legs.crew_wait > 0].sort_values("crew_wait", ascending=False)
        st.markdown(f"**{name}**: {len(held)} legs waited for a late crew")
        if len(held):
            show = held[["crew_row", "flight", "route", "sched_dep", "sim_dep", "own_delay", "crew_wait", "crew_caused"]]
            st.dataframe(show.rename(columns={"crew_row": "crew", "crew_wait": "waited for crew (min)",
                                              "crew_caused": "crew-caused incl. knock-on (min)",
                                              "own_delay": "own delay (min)"}),
                         hide_index=True, width="stretch")
