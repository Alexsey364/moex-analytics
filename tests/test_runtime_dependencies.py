import importlib.util
import sys
from pathlib import Path

SCRIPT = Path("scripts/check_runtime_dependencies.py")


def _module():
    name = "moex_runtime_preflight_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_required_runtime_dependencies_cover_application_imports():
    module = _module()
    names = {item.import_name for item in module.REQUIRED}
    assert {
        "duckdb",
        "streamlit",
        "pandas",
        "numpy",
        "sklearn",
        "plotly",
        "pyarrow",
        "scipy",
        "joblib",
        "requests",
        "yaml",
        "openpyxl",
    } <= names


def test_preflight_passes_when_environment_is_complete(monkeypatch, capsys):
    module = _module()
    monkeypatch.setattr(module, "missing_dependencies", lambda: [])
    assert module.main([]) == 0
    output = capsys.readouterr().out
    assert "Все зависимости установлены" in output
    assert "Запуск аналитики" in output


def test_preflight_decline_does_not_install(monkeypatch, capsys):
    module = _module()
    missing = [module.Dependency("scikit-learn", "sklearn", "sklearn")]
    monkeypatch.setattr(module, "missing_dependencies", lambda: missing)
    monkeypatch.setattr("builtins.input", lambda _prompt: "N")
    monkeypatch.setattr(module, "install_project", lambda _root: (_ for _ in ()).throw(AssertionError()))
    assert module.main([]) == 1
    assert "Не найдены обязательные пакеты: scikit-learn" in capsys.readouterr().out


def test_preflight_without_interactive_input_fails_cleanly(monkeypatch, capsys):
    module = _module()
    missing = [module.Dependency("scikit-learn", "sklearn", "sklearn")]
    monkeypatch.setattr(module, "missing_dependencies", lambda: missing)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))
    assert module.main([]) == 1
    assert "Установка отменена" in capsys.readouterr().out


def test_preflight_installs_and_rechecks(monkeypatch):
    module = _module()
    missing = [module.Dependency("scikit-learn", "sklearn", "sklearn")]
    checks = iter([missing, []])
    installed = []
    monkeypatch.setattr(module, "missing_dependencies", lambda: next(checks))
    monkeypatch.setattr(module, "install_project", lambda root: installed.append(root) or True)
    assert module.main(["--yes", "--project-root", "."]) == 0
    assert installed == [Path.cwd()]


def test_preflight_reports_failed_repair(monkeypatch):
    module = _module()
    missing = [module.Dependency("scikit-learn", "sklearn", "sklearn")]
    monkeypatch.setattr(module, "missing_dependencies", lambda: missing)
    monkeypatch.setattr(module, "install_project", lambda _root: False)
    assert module.main(["--yes"]) == 1


def test_current_environment_has_all_required_runtime_dependencies():
    module = _module()
    assert module.missing_dependencies() == []
    assert importlib.util.find_spec("sklearn") is not None
    assert sys.version_info >= (3, 12)


def test_launchers_run_preflight_before_dashboard():
    content = Path("START_MOEX_ANALYTICS.bat").read_text(encoding="utf-8")
    preflight = content.index("scripts\\check_runtime_dependencies.py")
    launcher = content.index("moex_analytics.launcher")
    assert preflight < launcher
    assert "py -3.12 -m venv" in content
    assert 'set "PYTHON_EXE=' in content
    assert "(" not in "\n".join(line for line in content.splitlines() if not line.lstrip().startswith('"'))


def test_pyproject_declares_every_required_distribution():
    module = _module()
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8").lower()
    for item in module.REQUIRED:
        assert f'"{item.distribution.lower()}' in pyproject
