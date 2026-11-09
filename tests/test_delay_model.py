"""Delay-model helpers."""

import numpy as np
import pandas as pd

from robust_crew.delay_model import EXCEED_THRESHOLDS, calibration_table, route_exceedance


def test_route_exceedance_interpolates_and_clamps():
    rates = dict(zip([f"route_p_gt{t}" for t in EXCEED_THRESHOLDS], [0.4, 0.2, 0.1, 0.05, 0.01]))
    df = pd.DataFrame([{**rates, "slack_min": s} for s in [-10, 0, 7.5, 45, 500]])
    out = route_exceedance(df)
    np.testing.assert_allclose(out, [0.4, 0.4, 0.3, 0.075, 0.01])


def test_route_exceedance_missing_history_is_nan():
    df = pd.DataFrame([{**{f"route_p_gt{t}": np.nan for t in EXCEED_THRESHOLDS}, "slack_min": 10}])
    assert np.isnan(route_exceedance(df)[0])


def test_calibration_table_bins():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.2, 0.8, 0.9])
    tab = calibration_table(y, p, bins=2)
    assert tab == [{"pred": 0.15, "obs": 0.0, "n": 2}, {"pred": 0.85, "obs": 1.0, "n": 2}]
