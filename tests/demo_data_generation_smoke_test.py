from __future__ import annotations

import gc
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_demo_qc_data import (
    DEFAULT_SEED,
    generate_demo_data,
    validate_demo_data,
)


def test_demo_data_generator_smoke() -> None:
    with TemporaryDirectory(ignore_cleanup_errors=True) as tempdir:
        db_path = Path(tempdir) / "demo_data_generation_smoke.db"
        summaries = generate_demo_data(db_path=db_path, seed=DEFAULT_SEED, on_conflict="skip")
        validate_demo_data(summaries)

        summary_by_batch = {summary.batch_lot_no: summary for summary in summaries}
        assert summary_by_batch["DEMO-LJ-BUILD-202604"].building_records == 19
        assert summary_by_batch["DEMO-LJ-BUILD-202604"].has_outlier is True
        assert summary_by_batch["DEMO-LJ-FORMAL-202603"].formal_records == 50
        assert summary_by_batch["DEMO-ZS-BUILD-202604"].building_records == 19
        assert summary_by_batch["DEMO-ZS-BUILD-202604"].has_outlier is True
        assert summary_by_batch["DEMO-ZS-FORMAL-202603"].formal_records == 50
        assert summary_by_batch["DEMO-INSTANT-BUILD-202604"].effective_records == 19
        assert summary_by_batch["DEMO-INSTANT-BUILD-202604"].has_outlier is True
        gc.collect()


if __name__ == "__main__":
    test_demo_data_generator_smoke()
    print("demo_data_generation_smoke_test passed")
