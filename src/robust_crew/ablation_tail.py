"""Sensitivity: planning model without tail-derived features (BTS tail = actual, not planned).

Usage: uv run python -m robust_crew.ablation_tail  (after delay_model train)
"""
import json

import pandas as pd
from robust_crew import config
from robust_crew.ingest import connect
from robust_crew import delay_model as dm

con = connect(); df = dm.load_features(con); con.close()
df = df[~df.excluded].copy(); df["FlightDate"] = pd.to_datetime(df["FlightDate"])
train, calib = df[df.split == "train"], df[df.split == "calib"]
feats = [f for f in dm.PLANNING_FEATURES if f not in ("same_tail", "tail_prior_turn_min", "tail_leg_seq")]
m = dm.fit_model("planning_no_tail", feats, train, calib)
out = {}
for s in ["calib", *config.TEST_WINDOWS]:
    d = df[df.split == s]
    out[s] = dm.metrics(d.broke.to_numpy(), m.predict_proba(d))
rep = json.loads((config.REPORTS_DIR / "phase4_delay_model.json").read_text())
rep["metrics_planning_without_tail_features"] = out
(config.REPORTS_DIR / "phase4_delay_model.json").write_text(json.dumps(rep, indent=2))
print(json.dumps(out, indent=1))
