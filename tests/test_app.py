"""Smoke test: the Streamlit explorer renders without exceptions (needs the local database)."""

import pytest

from robust_crew import config

streamlit = pytest.importorskip("streamlit")


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="no local database built yet")
def test_app_runs_in_both_windows_with_and_without_repair():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(config.ROOT / "app" / "streamlit_app.py"), default_timeout=180).run()
    assert not at.exception
    assert [m.label for m in at.metric][:4] == ["Crews", "Crew delay (min)", "Broken conn.", "Duty violations"]
    at.toggle[0].set_value(True).run()  # repair on
    assert not at.exception and "Reserves" in [m.label for m in at.metric]
    at.radio[0].set_value("test_winter").run()
    assert not at.exception
