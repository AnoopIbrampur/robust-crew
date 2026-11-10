"""Smoke test: the Streamlit explorer renders without exceptions (needs the local database)."""

import pytest

from robust_crew import config

streamlit = pytest.importorskip("streamlit")


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="no local database built yet")
def test_app_runs_in_both_seasons_with_and_without_repair():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(config.ROOT / "app" / "streamlit_app.py"), default_timeout=180).run()
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "Broken connections" in page and "Reserves called" not in page

    at.session_state["mode"] = "With repair"
    at.run()
    assert not at.exception
    assert "Reserves called" in " ".join(m.value for m in at.markdown)

    at.session_state["season"] = "Winter"
    at.session_state["view"] = "Cheapest plan"
    at.run()
    assert not at.exception
