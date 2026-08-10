import pandas as pd

from moex_analytics.dashboard.pages.predictive_command_center import (
    _opportunity_scatter,
    _ranking_board,
    _term_structure,
    evidence_label,
    load_visual_lab,
)


class FakeConnection:
    def __init__(self, frames):
        self.frames = iter(frames)

    def execute(self, *_args, **_kwargs):
        return self

    def df(self):
        return next(self.frames)


def test_visual_lab_reads_only_persisted_frames():
    frames = [
        pd.DataFrame({"secid": ["SBERP"], "horizon": [60]}),
        pd.DataFrame({"secid": ["SBERP"], "horizon": [60]}),
        pd.DataFrame({"secid": ["SBERP"], "horizon": [60]}),
        pd.DataFrame({"tranche": [100_000], "plan_rank": [1]}),
    ]
    result = load_visual_lab(FakeConnection(frames))
    assert result["ready"] is True
    assert set(result) == {"ranking", "distributions", "opportunity", "plans", "ready"}


def test_visual_lab_figures_keep_uncertainty_and_human_labels():
    ranking = pd.DataFrame({
        "secid": ["A"], "horizon": [60], "relative_rank": [.7],
        "rank_low": [.5], "rank_high": [.8], "tie_group": [1],
    })
    distributions = pd.DataFrame({
        "secid": ["A"], "horizon": [60], "q10_return": [-.2], "q25_return": [-.1],
        "q50_return": [.02], "q75_return": [.1], "q90_return": [.2],
    })
    opportunity = pd.DataFrame({
        "secid": ["A"], "horizon": [60], "downside_axis": [.2],
        "opportunity_axis": [.3], "portfolio_weight": [.1], "abstain": [True],
        "evidence_quality": ["research_oos"], "timing_status": ["wait"],
        "quadrant": ["watch"], "abstention_reason": ["no evidence"],
    })
    assert len(_ranking_board(ranking).data) == 1
    assert len(_term_structure(distributions, "A").data) == 5
    assert len(_opportunity_scatter(opportunity).data) == 1
    assert evidence_label("NO_EVIDENCE").startswith("⚪")
