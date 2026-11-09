"""Sensitivity: planning model without tail-derived features (BTS tail = actual, not planned).

Adds ``metrics_planning_without_tail_features`` to reports/phase4_delay_model.json.

Usage::

    uv run python -m robust_crew.ablation_tail   # after delay_model train
"""

from __future__ import annotations

import json

import pandas as pd

from robust_crew import config
from robust_crew import delay_model as dm
from robust_crew.ingest import connect

TAIL_FEATURES = ("same_tail", "tail_prior_turn_min", "tail_leg_seq")


def main() -> dict:
    """Refit the planning model without tail features and record its metrics."""
    con = connect()
    df = dm.load_features(con)
    con.close()
    df = df[~df.excluded].copy()
    df["FlightDate"] = pd.to_datetime(df["FlightDate"])
    train, calib = df[df.split == "train"], df[df.split == "calib"]
    feats = [f for f in dm.PLANNING_FEATURES if f not in TAIL_FEATURES]
    model = dm.fit_model("planning_no_tail", feats, train, calib)
    out = {}
    for split in ["calib", *config.TEST_WINDOWS]:
        d = df[df.split == split]
        out[split] = dm.metrics(d.broke.to_numpy(), model.predict_proba(d))
    path = config.REPORTS_DIR / "phase4_delay_model.json"
    rep = json.loads(path.read_text())
    rep["metrics_planning_without_tail_features"] = out
    path.write_text(json.dumps(rep, indent=2))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
