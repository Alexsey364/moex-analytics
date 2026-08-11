from pathlib import Path

import duckdb
import pytest

from moex_analytics.news_foundation import core


def test_registry_is_unique_governed_and_metadata_only():
    con = duckdb.connect(":memory:")
    result = core.load_source_registry(con)
    assert result == {"sources": 8, "active": 5, "disabled": 3, "duplicates": 0,
                      "full_text_retention": False}
    assert core.source_status(con) == {"sources": 8, "active": 5, "official": 8}
    assert con.execute("SELECT count(*) FROM news_source_registry WHERE status LIKE 'active%' "
                       "AND license LIKE '%review_required%'").fetchone()[0] == 0


def test_duplicate_and_ungoverned_active_sources_are_rejected(tmp_path: Path):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("sources:\n- &x {source_id: x, endpoint: u, status: disabled, "
                         "license: review_required, robots_status: review_required}\n- *x\n",
                         encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        core.load_source_registry(duckdb.connect(":memory:"), duplicate)
    with pytest.raises(ValueError, match="ungoverned"):
        core._validate([{"source_id": "x", "endpoint": "u", "status": "active_metadata_only",
                         "license": "review_required", "robots_status": "allowed"}])
