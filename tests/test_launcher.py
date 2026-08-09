from types import SimpleNamespace

import moex_analytics.launcher as launcher


def test_environment_check_accepts_python312_and_reports_missing(monkeypatch):
    monkeypatch.setattr(launcher.sys, "version_info", (3, 12, 10))
    monkeypatch.setattr(
        launcher.importlib.util,
        "find_spec",
        lambda name: None if name == "sklearn" else object(),
    )
    assert launcher.environment_errors() == ["Отсутствуют обязательные библиотеки: sklearn"]


def test_existing_dashboard_is_reused(monkeypatch):
    monkeypatch.setattr(launcher.port_launcher, "port_owner", lambda: (42, "dashboard"))
    monkeypatch.setattr(launcher.port_launcher, "classify_owner", lambda _owner: "dashboard")
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not start")),
    )
    assert launcher.start_dashboard() == (
        True,
        "Dashboard уже работает: http://localhost:8501",
    )


def test_foreign_port_is_not_killed(monkeypatch):
    monkeypatch.setattr(launcher.port_launcher, "port_owner", lambda: (77, "foreign"))
    monkeypatch.setattr(launcher.port_launcher, "classify_owner", lambda _owner: "other")
    assert launcher.start_dashboard() == (False, "Порт 8501 занят другим процессом. PID: 77")


def test_free_port_starts_one_dashboard(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.port_launcher, "port_owner", lambda: None)
    monkeypatch.setattr(launcher.port_launcher, "classify_owner", lambda _owner: "free")
    monkeypatch.setattr(launcher.port_launcher, "_mark_pending", lambda: calls.append("pending"))
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(pid=10),
    )
    monkeypatch.setattr(launcher, "wait_until_healthy", lambda: True)
    assert launcher.start_dashboard()[0] is True
    assert calls[0] == "pending"
    assert len(calls) == 2


def test_main_runs_daily_dashboard_and_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher, "environment_errors", lambda: [])
    monkeypatch.setattr(launcher, "run_quick_daily", lambda: True)
    monkeypatch.setattr(launcher, "start_dashboard", lambda: (True, "ok"))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    assert launcher.main([]) == 0
    assert opened == ["http://localhost:8501"]


def test_main_stops_on_environment_or_foreign_port(monkeypatch):
    monkeypatch.setattr(launcher, "environment_errors", lambda: ["bad"])
    assert launcher.main(["--skip-daily", "--no-browser"]) == 2
    monkeypatch.setattr(launcher, "environment_errors", lambda: [])
    monkeypatch.setattr(launcher, "start_dashboard", lambda: (False, "busy"))
    assert launcher.main(["--skip-daily", "--no-browser"]) == 2
