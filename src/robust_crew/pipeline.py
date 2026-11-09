"""Run the whole pipeline end to end (downloads ~650 MB from BTS on first run).

Usage::

    uv run python -m robust_crew.pipeline
"""

from __future__ import annotations

import logging

from robust_crew import (
    ablation_tail, delay_model, experiments, ingest, network, optimize, pairings, quality, repair, simulate, tiebreak,
)


def main() -> None:
    """Every stage in order; each writes its tables and its reports/ file."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ingest.ingest_bts()
    con = ingest.connect()
    ingest.build_flights(con)
    con.close()
    ingest.ingest_weather()
    quality.main()  # Phase 1 report
    network.main()  # Phase 2: aircraft rotations
    pairings.main()  # Phase 3 report
    con = ingest.connect()
    delay_model.build_connections(con)
    con.close()
    delay_model.train_and_evaluate()  # Phase 4
    ablation_tail.main()
    delay_model.predict_risk()
    optimize.main()  # Phase 5
    simulate.main()  # Phase 6
    repair.main()  # Phase 7
    tiebreak.main()  # baseline tie-break sensitivity
    experiments.main()  # Phase 8: figures, tables, README results block


if __name__ == "__main__":
    main()
