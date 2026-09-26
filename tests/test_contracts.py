from pathlib import Path

import pytest

from app.schemas import ReviewReport

CONTRACTS = sorted((Path(__file__).parent.parent / "contracts").glob("report_*.json"))


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.name)
def test_fixture_matches_schema_and_is_labeled(path: Path):
    report = ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert report.fixture is True
