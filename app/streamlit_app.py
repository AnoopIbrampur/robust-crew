"""Crew Explorer: replay a real day of crew operations through two crew schedules.

Visual style follows DrugSupply Watch: a bulletin in paper and ink. Structure comes from
type, space and hairlines rather than boxes; colour is reserved for disruption and every
colour has a text label beside it. Light and dark follow the system setting
(.streamlit/config.toml).

Run from the repository root:

    uv sync --extra app
    uv run streamlit run app/streamlit_app.py

It uses the full pipeline database (data/robust_crew.duckdb) when present, otherwise the
small committed extract app/demo.duckdb (built by ``python -m robust_crew.demo_extract``),
which is what the hosted copy runs on.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

try:
    from robust_crew import config
except ModuleNotFoundError:  # hosted without the package installed: use the source tree
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from robust_crew import config
from robust_crew.repair import replay_with_repair
from robust_crew.simulate import crew_caused_delay, load_day_flights, replay

DB = Path(os.environ.get("CREW_EXPLORER_DB") or (config.DB_PATH if config.DB_PATH.exists() else config.DEMO_DB_PATH))
ET = ZoneInfo("America/New_York")
REPO = "https://github.com/AnoopIbrampur/robust-crew"
SANS = "Public Sans, system-ui, -apple-system, Segoe UI, sans-serif"

# Disruption bands, low to high. Mid-tone fills read on both paper and ink backgrounds.
BANDS = [("None", 0, "#8d96a3"), ("Minor", 15, "#c9971a"), ("Moderate", 60, "#d0662a"), ("Severe", 10_000, "#c22d36")]
BAND_CLASS = {"None": "typical", "Minor": "moderate", "Moderate": "elevated", "Severe": "high"}

st.set_page_config(page_title="Crew Explorer", page_icon="✈", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown(
    """
<style>
:root {
  --bg: #f8f7f3; --tint: #f1eee6; --ink: #18202b; --muted: #535d6b;
  --line: #dedbd1; --line-strong: #bdb9ad; --accent: #0a5a8c;
  --typical: #55606e; --moderate: #7d5700; --elevated: #ad440c; --high: #ad1420;
  --typical-bg: #eceef0; --moderate-bg: #fbf0d2; --elevated-bg: #fce6d6; --high-bg: #fbe1e1;
  --better: #1f6b3a;
  --serif: "Newsreader", Georgia, "Times New Roman", serif;
  --sans: "Public Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --s1: 0.25rem; --s2: 0.5rem; --s3: 0.75rem; --s4: 1rem; --s6: 1.5rem; --s8: 2rem; --s12: 3rem;
  --t-xs: 0.8125rem; --t-sm: 0.875rem; --t-md: 1rem; --t-lg: 1.125rem; --t-xl: 1.5rem;
  --t-2xl: 2rem; --t-3xl: 3.5rem; --radius: 4px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12161c; --tint: #1b2129; --ink: #e9eae6; --muted: #a6afba;
    --line: #2f3742; --line-strong: #47515e; --accent: #8fc6ef;
    --typical: #aeb7c2; --moderate: #e8c25e; --elevated: #f6a56a; --high: #ff9090;
    --typical-bg: #28303a; --moderate-bg: #3a2f10; --elevated-bg: #40291a; --high-bg: #431f22;
    --better: #7fd19b;
  }
}

/* Streamlit chrome off; one measured column like the bulletin */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent; height: 0; }
.block-container { max-width: 70rem; padding: 0 var(--s6) var(--s8) !important; }
.stApp a { color: var(--accent); text-underline-offset: 0.15em; text-decoration-thickness: 1px; }
.stApp a:hover { text-decoration-thickness: 2px; }
.stApp a:active { opacity: 0.6; }

/* Masthead and disclaimer */
/* Full-bleed bands whose text stays aligned to the column */
.masthead, .disclaimer { box-shadow: 0 0 0 100vmax var(--band); clip-path: inset(0 -100vmax); }
.masthead { --band: var(--bg); display: flex; flex-wrap: wrap; align-items: center; gap: 0 var(--s8);
  min-height: 2.75rem; border-bottom: 1px solid var(--line); margin: 0; padding: var(--s1) 0 0; }
.brand { display: inline-flex; align-items: center; gap: var(--s2); color: var(--ink) !important;
  text-decoration: none !important; font: 600 var(--t-xl)/1 var(--serif); letter-spacing: -0.01em; }
.brand svg { width: var(--s6); height: var(--s6); color: var(--high); }
.masthead nav { display: flex; flex-wrap: wrap; margin-left: auto; }
.masthead nav a { display: inline-flex; align-items: center; min-height: 2.75rem; padding: 0 var(--s3);
  color: var(--muted) !important; text-decoration: none !important; font-size: var(--t-sm); }
.masthead nav a:hover { color: var(--ink) !important; }
.masthead nav a[aria-current] { color: var(--ink) !important; font-weight: 600; box-shadow: inset 0 -2px 0 var(--ink); }
.disclaimer { --band: var(--tint); background: var(--tint); margin: 0 0 var(--s4); padding: var(--s2) 0;
  font-size: var(--t-xs); line-height: 1.45; color: var(--muted); }
.disclaimer strong { color: var(--ink); }

/* Type: tight and close when large, open when small */
.dateline, .note, .foot { font-size: var(--t-sm); line-height: 1.5; letter-spacing: 0.005em; color: var(--muted); }
.dateline { margin: var(--s8) 0 var(--s1); }
h1.title { font: 600 clamp(var(--t-2xl), 1.6rem + 2vw, var(--t-3xl))/1.08 var(--serif) !important;
  letter-spacing: -0.02em; margin: 0 0 var(--s3) !important; padding: 0 !important; color: var(--ink); }
.lede { font-size: var(--t-lg); line-height: 1.5; max-width: 60ch; color: var(--ink); margin: 0; }
h2.sec { font: 600 var(--t-xl)/1.2 var(--serif) !important; letter-spacing: -0.01em;
  margin: var(--s12) 0 var(--s2) !important; padding: 0 !important; color: var(--ink); }
.note { margin: 0 0 var(--s3); }

/* Key numbers: a line of type between 2 rules */
.stats { list-style: none; padding: var(--s3) 0; margin: var(--s6) 0 var(--s4); display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)); gap: var(--s3) var(--s6);
  border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.stats li { font-size: var(--t-sm); color: var(--muted); line-height: 1.35; margin: 0; }
.stats strong { display: block; font: 600 var(--t-xl)/1.2 var(--serif); color: var(--ink);
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.stats .chg { display: block; font-size: var(--t-xs); font-variant-numeric: tabular-nums; margin-top: 2px; }
.chg.better { color: var(--better); } .chg.worse { color: var(--high); }

/* What changed: two columns of plain sentences */
.changes { display: flex; flex-wrap: wrap; gap: var(--s4) var(--s8); margin: var(--s4) 0 0; }
.changes > div { flex: 1 1 15rem; }
.changes h3 { font: 600 var(--t-md)/1.3 var(--sans); margin: 0 0 var(--s1); color: var(--ink); }
.changes p { font-size: var(--t-sm); line-height: 1.5; margin: 0; color: var(--ink); }

/* Disruption bands */
.typical { --c: var(--typical); --c-bg: var(--typical-bg); }
.moderate { --c: var(--moderate); --c-bg: var(--moderate-bg); }
.elevated { --c: var(--elevated); --c-bg: var(--elevated-bg); }
.high { --c: var(--high); --c-bg: var(--high-bg); }
.badge { display: inline-block; padding: 0 var(--s2); border-radius: var(--radius); white-space: nowrap;
  font-size: var(--t-xs); font-weight: 600; color: var(--c); background: var(--c-bg); }
.scale { list-style: none; padding: 0; margin: var(--s2) 0 var(--s3); display: flex; flex-wrap: wrap; gap: var(--s2) var(--s6); }
.scale li { font-size: var(--t-xs); color: var(--muted); margin: 0; }
.scale .badge { margin-right: var(--s1); }

/* Tables: rules only */
.table-wrap { overflow-x: auto; }
table.rules { border-collapse: collapse; width: 100%; font-size: var(--t-sm); line-height: 1.4; }
.rules th, .rules td { text-align: left; padding: var(--s2) var(--s3); border: 0; border-bottom: 1px solid var(--line);
  vertical-align: baseline; color: var(--ink); background: transparent; }
.rules th:first-child, .rules td:first-child { padding-left: 0; }
.rules th:last-child, .rules td:last-child { padding-right: 0; }
.rules th { font-size: var(--t-xs); font-weight: 500; color: var(--muted); white-space: nowrap;
  border-bottom-color: var(--line-strong); }
.rules .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }

/* Widgets: labels like the bulletin's form labels */
[data-testid="stWidgetLabel"] p { font-size: var(--t-sm) !important; font-weight: 600 !important; color: var(--ink); }
[data-testid="stVegaLiteChart"] { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: var(--s3) 0; }
[data-testid="stExpander"] details { border: 0 !important; border-top: 1px solid var(--line) !important; border-radius: 0 !important; }
[data-testid="stExpander"] summary { padding-left: 0 !important; color: var(--accent); }
[data-testid="stExpander"] summary p { font-size: var(--t-sm) !important; }

.foot { margin-top: var(--s12); box-shadow: 0 -1px 0 var(--line); padding-top: var(--s4); }
.foot p { margin: 0 0 var(--s2); }

@media (prefers-contrast: more) { :root { --line: var(--muted); --line-strong: var(--ink); } .note, .dateline, .foot { color: var(--ink); } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
""",
    unsafe_allow_html=True,
)


# --- Data --------------------------------------------------------------------------

@st.cache_resource
def db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB), read_only=True)
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
        "crews": len(pairings) + reserves, "delay": round(crew_caused_delay(res, base)),
        "broken": res.broken_connections, "duty": res.duty_violations, "replaced": res.crew_replacements,
        "swaps": swaps, "reserves": reserves,
    }
    return legs, m


# --- Presentation helpers ------------------------------------------------------------

def band(minutes: float) -> str:
    """Disruption band for a crew-caused delay in minutes."""
    if minutes <= 0:
        return "None"
    return next(name for name, hi, _ in BANDS[1:] if minutes <= hi)


def stat(value: int, label: str, base: int | None = None, unit: str = "") -> str:
    """One key number: figure, label, and the change against the cheapest plan in words."""
    chg = ""
    if base is not None:
        d = value - base
        if d == 0:
            chg = '<span class="chg">same as cheapest plan</span>'
        else:
            cls = "better" if d < 0 else "worse"
            chg = (f'<span class="chg {cls}">{"↓" if d < 0 else "↑"} {abs(d):,} '
                   f'{"fewer" if d < 0 else "more"} than cheapest ({base:,})</span>')
    return f"<li><strong>{value:,}{unit}</strong>{escape(label)}{chg}</li>"


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'s' if n != 1 else ''}"


def change_sentences(b: dict, s: dict, repair: bool) -> tuple[str, str]:
    """Two plain sentences: what the robust plan changed, and what it cost."""
    dbk, dd, dc = s["broken"] - b["broken"], s["delay"] - b["delay"], s["crews"] - b["crews"]
    bits = []
    if dbk:
        n = abs(dbk)
        bits.append(f"{n} {'fewer' if dbk < 0 else 'more'} broken connection{'s' if n != 1 else ''}")
    if dd:
        bits.append(f"{abs(dd):,} {'fewer' if dd < 0 else 'more'} minutes of crew-caused delay")
    effect = (" and ".join(bits) + " than the cheapest plan.") if bits else "No change in disruption."
    cost = ("The same number of crews" if dc == 0 else
            f"{abs(dc)} {'fewer' if dc < 0 else 'more'} crew{'s' if abs(dc) != 1 else ''}")
    cost += ", with day-of repair running on both plans." if repair else ", before any day-of repair."
    return effect[0].upper() + effect[1:], cost


def gantt(legs: pd.DataFrame, only_affected: bool, day: date) -> tuple[alt.Chart, int]:
    """Crew-by-crew timeline (grey: schedule, colour: replay) and how many shown legs run off the edge."""
    d = legs
    if only_affected:
        hit = d.groupby("crew_row").crew_caused.max()
        d = d[d.crew_row.isin(hit[hit > 0].index)]
    if d.empty:
        return alt.Chart(pd.DataFrame({"t": ["No crew caused any delay on this day."]})).mark_text(
            size=14, font=SANS, color="#8d96a3").encode(text="t").properties(height=60), 0
    d = d.assign(impact=d.crew_caused.map(band))
    lo = pd.Timestamp(datetime(day.year, day.month, day.day, 4), tz=ET)
    hi = lo + pd.Timedelta(hours=28)
    xs = alt.Scale(domain=[lo.isoformat(), hi.isoformat()])
    y = alt.Y("crew_row:O", title=None, sort="ascending", axis=None)
    tip = [alt.Tooltip("flight:N", title="Flight"), alt.Tooltip("route:N", title="Route"),
           alt.Tooltip("sched_dep:T", title="Scheduled", format="%H:%M"),
           alt.Tooltip("sim_dep:T", title="Replayed", format="%H:%M"),
           alt.Tooltip("own_delay:Q", title="Own delay (min)"),
           alt.Tooltip("crew_wait:Q", title="Waited for crew (min)"),
           alt.Tooltip("crew_caused:Q", title="Crew-caused, incl. knock-on (min)")]
    sched = alt.Chart(d).mark_bar(color="#a7a397", opacity=0.55, height=3, clip=True).encode(
        x=alt.X("sched_dep:T", title=None, scale=xs,
                axis=alt.Axis(format="%H:%M", tickCount=10, labelAngle=0, grid=True, domain=False, ticks=False)),
        x2="sched_arr:T", y=y, tooltip=tip)
    sim = alt.Chart(d).mark_bar(height=9, cornerRadius=2, clip=True).encode(
        x=alt.X("sim_dep:T", scale=xs), x2="sim_arr:T", y=y, tooltip=tip,
        color=alt.Color("impact:N", legend=None,
                        scale=alt.Scale(domain=[n for n, _, _ in BANDS], range=[c for _, _, c in BANDS])))
    late = int((d.sim_arr > hi).sum())
    chart = (sched + sim).properties(height=alt.Step(14)).configure(font=SANS, background="transparent") \
        .configure_view(stroke=None).configure_axis(labelFontSize=11, gridOpacity=0.5)
    return chart, late


def clock(t: pd.Timestamp, day: date) -> str:
    """HH:MM, with +Nd when the time falls on a later calendar day than the operating day."""
    lag = (t.date() - day).days
    return f"{t:%H:%M}" + (f' <span class="note">+{lag}d</span>' if lag > 0 else "")


def held_table(legs: pd.DataFrame, day: date) -> str:
    """Rules-only table of flights that waited for a late crew, worst first."""
    held = legs[legs.crew_wait > 0].sort_values("crew_wait", ascending=False)
    if held.empty:
        return '<p class="note">No flight waited for a late crew on this day.</p>'
    rows = []
    for r in held.itertuples():
        b = band(r.crew_caused)
        rows.append(
            f"<tr><td>{escape(r.flight)}</td><td>{escape(r.route)}</td>"
            f'<td class="num">{clock(r.sched_dep, day)}</td><td class="num">{clock(r.sim_dep, day)}</td>'
            f'<td class="num">{r.own_delay:,}</td><td class="num">{r.crew_wait:,}</td>'
            f'<td class="num">{r.crew_caused:,}</td><td><span class="badge {BAND_CLASS[b]}">{b}</span></td></tr>')
    return ('<div class="table-wrap"><table class="rules"><thead><tr><th>Flight</th><th>Route</th>'
            '<th class="num">Scheduled</th><th class="num">Replayed</th><th class="num">Own delay, min</th>'
            '<th class="num">Waited for crew, min</th><th class="num">Crew-caused, min</th><th>Band</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


# --- Page --------------------------------------------------------------------------

PLANE = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
         'stroke-linejoin="round" aria-hidden="true"><path d="M2 16l20-7-20-7 4 7-4 7z"/><path d="M6 9h7"/></svg>')
st.markdown(
    f'<div class="masthead"><a class="brand" href="{REPO}">{PLANE}Crew Explorer</a>'
    f'<nav><a aria-current="page" href="#">Replay a day</a><a href="{REPO}#results">Results</a>'
    f'<a href="{REPO}#limitations-honest-list">Method &amp; limits</a><a href="{REPO}">Source</a></nav></div>'
    '<p class="disclaimer">Crew Explorer replays <strong>real 2023 American Airlines flights</strong> at Charlotte '
    'through <strong>synthetic crew schedules</strong>. Crew rules are simplified from FAA Part 117. '
    'It is a research model, <strong>not an operational or compliance tool</strong>.</p>',
    unsafe_allow_html=True,
)

if not DB.exists():
    st.error("No database found. Run `uv run python -m robust_crew.pipeline` first.")
    st.stop()

days = test_days()
lams = soft_lambdas()
best = selected_lambda()

hero_l, hero_r = st.columns([1.55, 1], gap="large", vertical_alignment="bottom")
with hero_r:
    season = st.segmented_control("Test window", ["Summer", "Winter"], default="Summer", key="season") or "Summer"
    window = "test_winter" if season == "Winter" else "test_summer"
    wd = days[days.split == window].sort_values("crew_delay_min", ascending=False)
    labels = {pd.Timestamp(r.date).date(): f"{pd.Timestamp(r.date):%a %b %-d}  ·  {r.crew_delay_min / 60:,.0f} h delay"
              for r in wd.itertuples()}
    day = st.selectbox("Day, worst first", list(labels), format_func=labels.get)
    mode = st.segmented_control("Operations", ["Plan only", "With day-of repair"], default="Plan only",
                                key="mode") or "Plan only"
    repair = mode != "Plan only"
with hero_l:
    st.markdown(
        f'<p class="dateline">{season} 2023 test window · {day:%A, %B %-d}</p>'
        '<h1 class="title">Crew schedule replay</h1>'
        '<p class="lede">The same day of real flights, flown by two crew schedules: the <strong>cheapest</strong> '
        'one, and a <strong>robust</strong> one that steers crews away from connections likely to break. '
        'Late crews hold up the next flight; here is how often, and where.</p>',
        unsafe_allow_html=True,
    )

with st.expander("Settings: robustness weight and filters"):
    lam = st.select_slider("Robustness weight λ (pay hours per expected disrupted leg)", lams,
                           value=best if best in lams else lams[0])
    if best is not None:
        st.markdown(f'<p class="note">λ = {best:g} was chosen on calibration days, before any test day was seen. '
                    'Higher values buy more robustness with more crews.</p>', unsafe_allow_html=True)
    only_affected = st.toggle("Show only crews that caused delay", value=True)

base_legs, b = run(day, pairings_for(day, "baseline", None), repair)
soft_legs, s = run(day, pairings_for(day, "soft", lam), repair)

stats = [stat(s["broken"], "broken crew connections", b["broken"]),
         stat(s["delay"], "minutes of crew-caused delay", b["delay"]),
         stat(s["crews"], "crews flying", b["crews"]),
         stat(s["replaced"], "crews replaced at the duty limit", b["replaced"]),
         stat(s["duty"], "duty-limit overruns in the air", b["duty"])]
if repair:
    stats += [stat(s["reserves"], "reserve crews called", b["reserves"]), stat(s["swaps"], "crew swaps", b["swaps"])]
effect, cost = change_sentences(b, s, repair)
st.markdown(
    f'<ul class="stats">{"".join(stats)}</ul>'
    '<h2 class="sec">What the robust plan changed</h2>'
    f'<div class="changes"><div><h3>Disruption</h3><p>{escape(effect)}</p></div>'
    f'<div><h3>Cost</h3><p>{escape(cost)}</p></div></div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<h2 class="sec">Crew timelines</h2>'
    '<p class="note">Each row is one crew’s day. The thin grey line is the schedule; the bar is the same leg replayed '
    'with the delays that actually happened, banded by how much delay that crew caused, counting knock-on through '
    'the aircraft.</p>'
    '<ul class="scale">'
    + "".join(f'<li><span class="badge {BAND_CLASS[n]}">{n}</span>'
              f'{"no crew-caused delay" if n == "None" else ("1–15 min" if n == "Minor" else ("16–60 min" if n == "Moderate" else "over 60 min"))}</li>'
              for n, _, _ in BANDS)
    + "</ul>",
    unsafe_allow_html=True,
)
view = st.segmented_control("Schedule shown", ["Robust plan", "Cheapest plan"], default="Robust plan",
                            key="view", label_visibility="collapsed") or "Robust plan"
legs = base_legs if view == "Cheapest plan" else soft_legs
chart, late = gantt(legs, only_affected, day)
st.altair_chart(chart, width="stretch")
if late:
    st.markdown(f'<p class="note">{plural(late, "leg")} finished after 08:00 the next morning and '
                f'{"run" if late != 1 else "runs"} off the right edge.</p>', unsafe_allow_html=True)

st.markdown(f'<h2 class="sec">Flights that waited for a late crew</h2>'
            f'<p class="note">{view}, worst first. Own delay is the flight’s delay before any crew effect.</p>'
            + held_table(legs, day), unsafe_allow_html=True)

st.markdown(
    '<div class="foot"><p>Data: BTS Reporting Carrier On-Time Performance and Iowa Environmental Mesonet ASOS, '
    '2022–2023. Schedules come from a CP-SAT set-partitioning model; break risk from a LightGBM model trained '
    'only on earlier months.</p>'
    f'<p><a href="{REPO}#limitations-honest-list">Method and limits</a> · <a href="{REPO}">Source on GitHub</a></p></div>',
    unsafe_allow_html=True,
)
