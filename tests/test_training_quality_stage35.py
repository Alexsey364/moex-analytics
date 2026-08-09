from moex_analytics.training_quality.issuer_context import ISSUERS
from moex_analytics.training_quality.schema import DDL


def test_issuer_context_keeps_x5_and_five_separate_and_is_research_only():
    assert ISSUERS["X5"][0] == ("X5",)
    assert ISSUERS["FIVE"][0] == ("FIVE",)
    assert "issuer_pit_fundamental_states" in DDL
    assert "issuer_sector_context_daily" in DDL
    assert "production_changes INTEGER" in DDL
