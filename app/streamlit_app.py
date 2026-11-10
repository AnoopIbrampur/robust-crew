"""robust-crew explorer: baseline vs robust crew schedules on a real test day.

Run after the pipeline has built data/robust_crew.duckdb:

    uv sync --extra app
    uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from robust_crew import config
from robust_crew.repair import replay_with_repair
from robust_crew.simulate import crew_caused_delay, load_day_flights, replay

ET = ZoneInfo("America/New_York")
FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', Helvetica, Arial, sans-serif"

# Apple system colours (light appearance). One accent; the delay ramp reads calm -> urgent.
GRAY = "#86868b"
IMPACT = {"On time": "#0a84ff", "1–15 min": "#ffcc00", "16–60 min": "#ff9500", "Over 60 min": "#ff3b30"}

st.set_page_config(page_title="Crew Explorer · robust-crew", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    f"""
<style>
:root {{
  --text: #1d1d1f; --secondary: #6e6e73; --tertiary: #86868b;
  --surface: #ffffff; --canvas: #f5f5f7; --hairline: rgba(0,0,0,0.08);
  --green: #248a3d; --red: #d70015;
}}
.stApp, .stApp *:not([data-testid="stIconMaterial"]):not(.material-symbols-rounded) {{ font-family: {FONT} !important; }}
[data-testid="stIconMaterial"], .material-symbols-rounded {{ font-family: "Material Symbols Rounded" !important; }}
.stApp {{ background: var(--canvas); color: var(--text); }}
#MainMenu, footer, header [data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none !important; }}
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
.block-container {{ max-width: 1180px; padding: 2.4rem 2rem 4rem; }}

.eyebrow {{ font-size: 0.8rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
            color: var(--tertiary); margin: 0 0 0.35rem; }}
.title {{ font-size: clamp(2rem, 4vw, 2.75rem); line-height: 1.05; letter-spacing: -0.025em;
          font-weight: 700; margin: 0; color: var(--text); padding: 0; }}
.lede {{ font-size: 1.05rem; line-height: 1.45; color: var(--secondary); margin: 0.6rem 0 1.6rem; max-width: 46rem; }}
.section {{ font-size: 1.35rem !important; line-height: 1.2; font-weight: 600; letter-spacing: -0.015em; margin: 2.2rem 0 0.25rem; color: var(--text); }}
.note {{ font-size: 0.85rem !important; line-height: 1.45; color: var(--tertiary); margin: 0.2rem 0 0; }}

.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-top: 1.1rem; }}
.card {{ background: var(--surface); border-radius: 16px; padding: 16px 18px 14px;
         box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 0 0 1px var(--hairline); }}
.card .label {{ font-size: 0.8rem; font-weight: 500; color: var(--secondary); white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; }}
.card .value {{ font-size: 1.9rem; font-weight: 600; letter-spacing: -0.02em; line-height: 1.15; color: var(--text);
                margin-top: 4px; font-variant-numeric: tabular-nums; }}
.card .unit {{ font-size: 0.95rem; font-weight: 500; color: var(--tertiary); margin-left: 3px; letter-spacing: 0; }}
.card .delta {{ font-size: 0.8rem; font-weight: 500; margin-top: 6px; color: var(--tertiary);
                font-variant-numeric: tabular-nums; }}
.delta.better {{ color: var(--green); }} .delta.worse {{ color: var(--red); }}

.insight {{ background: var(--surface); border-radius: 16px; padding: 14px 18px; margin-top: 12px; color: var(--text);
            box-shadow: 0 0 0 1px var(--hairline); font-size: 0.98rem; line-height: 1.45; }}

[data-testid="stExpander"] {{ border-radius: 14px; border: 1px solid var(--hairline); background: var(--surface); }}
[data-testid="stVegaLiteChart"] {{ background: var(--surface); border-radius: 16px; padding: 14px 10px 6px;
                                   box-shadow: 0 0 0 1px var(--hairline); }}

@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
""",
    unsafe_allow_html=True,
)


# --- Data --------------------------------------------------------------------------

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
        swaps, reserves = res.swaps, res.reserves_used
    else:
        res = replay(flights, pairings)
        crews = [[x for x in p if by_id[x].completed] for p in pairings]
        swaps = reserves = 0
    rows = []
    for c, legs in enumerate(crews):
        for pos, fid in enumerate(legs):
            f = by_id[fid]
            arr = res.sim_arr[fid]
            dep = arr - pd.Timedelta(minutes=f.actual_block)
            rows.append({
                "crew": c, "pos": pos, "flight": "AA " + fid.split("_")[2],
                "route": f"{f.origin} → {f.dest}", "tail": f.tail or "—",
                "sched_dep": f.sched_dep.astimezone(ET), "sched_arr": f.sched_arr.astimezone(ET),
                "sim_dep": dep.astimezone(ET), "sim_arr": arr.astimezone(ET),
                "own_delay": round(f.own_dep_delay),
                "crew_wait": round(res.crew_delay_at.get(fid, 0.0)),
                "crew_caused": round((arr - base.sim_arr[fid]).total_seconds() / 60),
            })
    legs = pd.DataFrame(rows)
    if len(legs):
        first = legs.groupby("crew").sched_dep.min().sort_values()
        legs["crew_row"] = legs.crew.map({c: i + 1 for i, c in enumerate(first.index)})
    m = {
        "crews": len(crews), "delay": round(crew_caused_delay(res, base)),
        "broken": res.broken_connections, "duty": res.duty_violations,
        "swaps": swaps, "reserves": reserves,
    }
    return legs, m


# --- Presentation helpers ------------------------------------------------------------

def card(label: str, value: int, unit: str, base: int | None, lower_is_better: bool = True) -> str:
    """A metric card; the delta line compares with the baseline."""
    delta = ""
    if base is not None:
        d = value - base
        if d == 0:
            delta = '<div class="delta">No change</div>'
        else:
            better = (d < 0) == lower_is_better
            arrow = "↓" if d < 0 else "↑"
            delta = (f'<div class="delta {"better" if better else "worse"}">{arrow} {abs(d):,} '
                     f'<span style="color:var(--tertiary)">vs {base:,}</span></div>')
    return (f'<div class="card"><div class="label">{escape(label)}</div>'
            f'<div class="value">{value:,}<span class="unit">{escape(unit)}</span></div>{delta}</div>')


def insight(b: dict, s: dict, lam: float, repair: bool) -> str:
    """One plain sentence that says what changed."""
    parts = []
    dbk, dd = s["broken"] - b["broken"], s["delay"] - b["delay"]
    if dbk < 0:
        parts.append(f"<b>{-dbk} fewer broken connections</b>")
    elif dbk > 0:
        parts.append(f"<b>{dbk} more broken connections</b>")
    if dd < 0:
        parts.append(f"<b>{-dd:,} fewer minutes</b> of crew-caused delay")
    elif dd > 0:
        parts.append(f"<b>{dd:,} more minutes</b> of crew-caused delay")
    crews = s["crews"] - b["crews"]
    plural = lambda n: f"{n} crew{'s' if n != 1 else ''}"  # noqa: E731
    cost = ("with the same number of crews" if crews == 0 else
            f"using {plural(-crews)} fewer" if crews < 0 else f"for {plural(crews)} more")
    what = " and ".join(parts) if parts else "no change in disruption"
    ctx = ", with day-of repair on both" if repair else ""
    return (f'<div class="insight">Against the cheapest plan, the robust plan (λ = {lam:g}) had {what}, '
            f'{cost}{ctx}.</div>')


def gantt(legs: pd.DataFrame, only_affected: bool, day: date) -> tuple[alt.Chart, int]:
    """Crew-by-crew timeline (grey: schedule, colour: replay) and how many shown legs run off the edge."""
    d = legs
    if only_affected:
        hit = d.groupby("crew_row").crew_caused.max()
        d = d[d.crew_row.isin(hit[hit > 0].index)]
    if d.empty:
        return alt.Chart(pd.DataFrame({"t": ["No crew-caused delay on this day."]})).mark_text(
            size=15, font=FONT, color=GRAY).encode(text="t").properties(height=80), 0
    d = d.assign(impact=pd.cut(d.crew_caused, [-1, 0, 15, 60, 10_000], labels=list(IMPACT)))
    lo = pd.Timestamp(datetime(day.year, day.month, day.day, 4), tz=ET)
    hi = lo + pd.Timedelta(hours=28)
    xs = alt.Scale(domain=[lo.isoformat(), hi.isoformat()])
    y = alt.Y("crew_row:O", title=None, sort="ascending",
              axis=alt.Axis(labels=False, ticks=False, domain=False))
    tip = [alt.Tooltip("flight:N", title="Flight"), alt.Tooltip("route:N", title="Route"),
           alt.Tooltip("sched_dep:T", title="Scheduled", format="%H:%M"),
           alt.Tooltip("sim_dep:T", title="Replayed", format="%H:%M"),
           alt.Tooltip("own_delay:Q", title="Own delay (min)"),
           alt.Tooltip("crew_wait:Q", title="Waited for crew (min)"),
           alt.Tooltip("crew_caused:Q", title="Crew-caused incl. knock-on (min)")]
    sched = alt.Chart(d).mark_bar(color="#d2d2d7", height=4, cornerRadius=2, clip=True).encode(
        x=alt.X("sched_dep:T", title=None, scale=xs,
                axis=alt.Axis(format="%H:%M", tickCount=10, labelAngle=0, grid=True)),
        x2="sched_arr:T", y=y, tooltip=tip)
    sim = alt.Chart(d).mark_bar(height=10, cornerRadius=3, clip=True).encode(
        x=alt.X("sim_dep:T", scale=xs), x2="sim_arr:T", y=y, tooltip=tip,
        color=alt.Color("impact:N", title=None,
                        scale=alt.Scale(domain=list(IMPACT), range=list(IMPACT.values())),
                        legend=alt.Legend(orient="top", direction="horizontal", symbolType="circle",
                                          symbolSize=90, labelFontSize=12, padding=0, offset=4)))
    late = int((d.sim_arr > hi).sum())
    return (sched + sim).properties(height=alt.Step(15)).configure(
        font=FONT, background="transparent",
    ).configure_view(stroke=None).configure_axis(
        labelColor="#6e6e73", labelFontSize=11, gridColor="#ececf0", domainColor="#d2d2d7", tickColor="#d2d2d7",
    ).configure_legend(labelColor="#1d1d1f"), late


# --- Page --------------------------------------------------------------------------

st.markdown(
    '<p class="eyebrow">robust-crew · American Airlines at Charlotte</p>'
    '<h1 class="title">Crew Explorer</h1>'
    '<p class="lede">Replay a real day of 2023 operations through two crew schedules: the cheapest one, '
    'and one that steers crews away from risky connections. See where late crews held up flights.</p>',
    unsafe_allow_html=True,
)

if not config.DB_PATH.exists():
    st.error("No database found. Run `uv run python -m robust_crew.pipeline` first.")
    st.stop()

days = test_days()
lams = soft_lambdas()
best = selected_lambda()

c1, c2, c3, c4 = st.columns([1.1, 2.2, 1.6, 0.9], vertical_alignment="bottom")
season = c1.segmented_control("Season", ["Summer", "Winter"], default="Summer", key="season") or "Summer"
window = "test_winter" if season == "Winter" else "test_summer"
wd = days[days.split == window].sort_values("crew_delay_min", ascending=False)
labels = {pd.Timestamp(r.date).date(): f"{pd.Timestamp(r.date):%a, %b %-d}  ·  {r.crew_delay_min / 60:,.0f} h baseline delay"
          for r in wd.itertuples()}
day = c2.selectbox("Day, worst first", list(labels), format_func=labels.get)
mode = c3.segmented_control("Operations", ["Plan only", "With repair"], default="Plan only", key="mode") or "Plan only"
repair = mode == "With repair"
with c4.popover("Settings", width="stretch"):
    lam = st.select_slider("Robustness weight λ", lams, value=best if best in lams else lams[0],
                           help="Pay hours charged per expected disrupted leg. Higher buys more robustness with more crews.")
    if best is not None:
        st.caption(f"λ = {best:g} was chosen on calibration days.")
    only_affected = st.toggle("Only crews hit by delay", value=True)

base_legs, b = run(day, pairings_for(day, "baseline", None), repair)
soft_legs, s = run(day, pairings_for(day, "soft", lam), repair)

st.markdown(f'<div class="section">{day:%A, %B %-d, %Y}</div>'
            f'<p class="note">Robust plan, compared with the cheapest plan on the same day'
            f'{" · day-of repair on" if repair else ""}.</p>', unsafe_allow_html=True)

cards = [
    card("Broken connections", s["broken"], "", b["broken"]),
    card("Crew-caused delay", s["delay"], "min", b["delay"]),
    card("Crews", s["crews"], "", b["crews"]),
    card("Duty overruns", s["duty"], "", b["duty"]),
]
if repair:
    cards.append(card("Reserves called", s["reserves"], "", b["reserves"]))
    cards.append(card("Swaps", s["swaps"], "", b["swaps"]))
st.markdown(f'<div class="cards">{"".join(cards)}</div>' + insight(b, s, lam, repair), unsafe_allow_html=True)

st.markdown('<div class="section">Crew timelines</div>'
            '<p class="note">Each row is one crew’s day. Grey is the schedule; colour is the replay with real '
            'delays, by how much delay the crew caused (including knock-on through the aircraft).</p>',
            unsafe_allow_html=True)
view = st.segmented_control("Schedule", ["Robust plan", "Cheapest plan"], default="Robust plan",
                            key="view", label_visibility="collapsed") or "Robust plan"
legs = base_legs if view == "Cheapest plan" else soft_legs
chart, late = gantt(legs, only_affected, day)
st.altair_chart(chart, width="stretch")
if late:
    st.markdown(f'<p class="note">{late} leg{"s" if late != 1 else ""} finished after 08:00 the next morning '
                'and run off the right edge.</p>',
                unsafe_allow_html=True)

with st.expander("Flights that waited for a late crew"):
    held = legs[legs.crew_wait > 0].sort_values("crew_wait", ascending=False)
    if held.empty:
        st.markdown('<p class="note">None on this day.</p>', unsafe_allow_html=True)
    else:
        show = held.assign(
            scheduled=held.sched_dep.dt.strftime("%H:%M"), replayed=held.sim_dep.dt.strftime("%H:%M"),
        )[["flight", "route", "scheduled", "replayed", "own_delay", "crew_wait", "crew_caused"]]
        st.dataframe(
            show, hide_index=True, width="stretch",
            column_config={
                "flight": "Flight", "route": "Route", "scheduled": "Scheduled", "replayed": "Replayed",
                "own_delay": st.column_config.NumberColumn("Own delay", format="%d min"),
                "crew_wait": st.column_config.NumberColumn("Waited for crew", format="%d min"),
                "crew_caused": st.column_config.NumberColumn("Crew-caused, incl. knock-on", format="%d min"),
            },
        )

st.markdown('<p class="note" style="margin-top:2.5rem">Crews are synthetic and the rules are simplified '
            '(FAA Part 117 duty tables only). This is a research model, not a compliance claim.</p>',
            unsafe_allow_html=True)
