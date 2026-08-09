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
    monkeypatch.setattr(launcher, "_pending_is_fresh", lambda: False)
    assert launcher.classify_owner(
        (11, "python -m streamlit run C:/repo/moex_analytics/dashboard/app.py --server.port 8501")
    ) == "dashboard"


def test_dashboard_launcher_marker_health_owner_and_main(tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace

    from moex_analytics.dashboard import launcher

    marker = tmp_path / "dashboard.json"
    launcher.mark_process(123, marker)
    assert json.loads(marker.read_text(encoding="utf-8"))["pid"] == 123
    assert launcher._marker_pid(marker) == 123
    marker.write_text("{}", encoding="utf-8")
    assert launcher._marker_pid(marker) is None
    launcher._mark_pending(marker)
    assert launcher._pending_is_fresh(marker)
    marker.write_text("not-json", encoding="utf-8")
    assert not launcher._pending_is_fresh(marker)

    class HealthyResponse:
        status = 200

        def read(self):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda *a, **k: HealthyResponse())
    assert launcher._healthy()
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
    )
    assert not launcher._healthy()

    calls = iter(
        [
            SimpleNamespace(stdout="TCP 127.0.0.1:8501 0.0.0.0:0 LISTENING 4321\n"),
            SimpleNamespace(stdout="python -m streamlit run moex_analytics/dashboard/app.py 8501"),
        ]
    )
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: next(calls))
    assert launcher.port_owner() == (
        4321,
        "python -m streamlit run moex_analytics/dashboard/app.py 8501",
    )

    monkeypatch.setattr(launcher, "port_owner", lambda: None)
    monkeypatch.setattr(launcher, "_mark_pending", lambda: None)
    assert launcher.main() == 3
    monkeypatch.setattr(launcher, "port_owner", lambda: (4321, "foreign"))
    monkeypatch.setattr(launcher, "classify_owner", lambda owner: "dashboard")
    assert launcher.main() == 0
    assert "уже работает" in capsys.readouterr().out
    monkeypatch.setattr(launcher, "classify_owner", lambda owner: "other")
    assert launcher.main() == 2
    assert "PID: 4321" in capsys.readouterr().out
