import ast
import importlib
import inspect
from pathlib import Path

import pandas as pd


def test_every_registered_dashboard_renderer_exists_with_zero_argument_signature():
    tree = ast.parse(Path("src/moex_analytics/dashboard/app.py").read_text(encoding="utf-8"))
    routes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for value in node.values:
                if (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.attr.startswith("render")
                ):
                    routes.append((value.value.id, value.attr))
    assert routes
    for module_name, renderer_name in routes:
        module = importlib.import_module(f"moex_analytics.dashboard.pages.{module_name}")
        renderer = getattr(module, renderer_name)
        assert callable(renderer)
        required = [
            parameter
            for parameter in inspect.signature(renderer).parameters.values()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]
        assert required == [], f"{module_name}.{renderer_name} requires arguments"


def test_stage15_pages_render_graceful_empty_state(monkeypatch):
    from moex_analytics.dashboard.pages import portfolio_research

    messages = []
    monkeypatch.setattr(portfolio_research, "_q", lambda _sql: pd.DataFrame())
    monkeypatch.setattr(portfolio_research.st, "header", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(portfolio_research.st, "warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(portfolio_research.st, "info", lambda message: messages.append(message))
    for renderer in (
        portfolio_research.render_fundamental_readiness,
        portfolio_research.render_company_valuation,
        portfolio_research.render_dividend_flow,
        portfolio_research.render_regime_risk_v15,
        portfolio_research.render_action_map,
        portfolio_research.render_alternatives_v15,
        portfolio_research.render_data_quality_v15,
    ):
        renderer()
    assert len(messages) == 7


def test_dashboard_launcher_classifies_port_owner(monkeypatch):
    from moex_analytics.dashboard import launcher

    monkeypatch.setattr(launcher, "_marker_pid", lambda: 10)
    monkeypatch.setattr(launcher, "_pending_is_fresh", lambda: False)
    monkeypatch.setattr(launcher, "_healthy", lambda: True)
    assert launcher.classify_owner(None) == "free"
    assert launcher.classify_owner((10, "")) == "dashboard"
    assert launcher.classify_owner((11, "")) == "other"
