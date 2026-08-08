from pathlib import Path

import duckdb
import pytest
import yaml

from moex_analytics.dashboard.navigation import ADVANCED_GROUPS, BASIC_LABELS, group_advanced_pages
from moex_analytics.portfolio_research.portfolio_editor import (
    load_positions,
    position_diff,
    save_positions,
    validate_positions,
)
from moex_analytics.portfolio_research.visual_assistant import (
    confidence_dots,
    plan_allocation,
    status_change,
    visual_status,
)


def _status(**overrides):
    values = {"action_group": "consider", "confidence": 80, "data_status": "sufficient"}
    values.update(overrides)
    return visual_status(**values)


def test_visual_status_mapping_and_accessible_confidence():
    assert _status() == "GREEN"
    assert _status(confidence=50) == "LIGHT_GREEN"
    assert _status(action_group="wait") == "YELLOW"
    assert _status(action_group="insufficient_data") == "GRAY"
    assert confidence_dots("средняя") == "●●○○ Средняя"


def test_rejected_alpha_cannot_be_green():
    assert _status(research_status="rejected") == "YELLOW"


def test_missing_fundamental_penalty_and_concentration_override():
    assert _status(fundamental_status="insufficient_data") == "ORANGE"
    assert _status(weight=0.30) == "RED"
    assert _status(risk_contribution=0.31) == "RED"


def test_status_change():
    assert status_change("GREEN", "YELLOW") == "↑ улучшилось"
    assert status_change("RED", "YELLOW") == "↓ ухудшилось"
    assert status_change("GRAY", "GRAY") == "→ без изменений"


def _row(quantity=41):
    return {"secid": "SBERP", "quantity": quantity, "average_price": 287.81,
            "allow_buy": True, "allow_sell": True, "frozen": False, "notes": ""}


def test_editor_load_validation_diff_atomic_save_and_backup(tmp_path):
    path, backups = tmp_path / "portfolio_positions.local.yaml", tmp_path / "backups"
    path.write_text(yaml.safe_dump({"mode": "real", "cash": None, "positions": [_row()]}), encoding="utf-8")
    before = path.read_bytes()
    loaded = load_positions(path)
    after = validate_positions([_row(42)], {"SBERP"})
    assert position_diff(loaded, after) == ["Было: SBERP 41 → Станет: SBERP 42"]
    backup = save_positions(after, {"SBERP"}, path, backups)
    assert backup and backup.read_bytes() == before
    assert load_positions(path)[0]["quantity"] == 42
    assert not list(tmp_path.glob(".portfolio.*"))


def test_editor_rejects_unknown_and_invalid_values():
    with pytest.raises(ValueError, match="не найден"):
        validate_positions([{**_row(), "secid": "UNKNOWN"}], {"SBERP"})
    with pytest.raises(ValueError, match="положительным"):
        validate_positions([_row(0)], {"SBERP"})
    assert validate_positions([{**_row(), "average_price": 0}], {"SBERP"})[0]["average_price"] == 0


def test_allocation_planner_partial_lot_rounding_and_reserve():
    candidates = [{"secid": "SBERP", "status": "GREEN", "price": 282.74,
                   "lot_size": 10, "liquidity_ok": True, "allow_buy": True}]
    plan = plan_allocation(100_000, candidates)
    assert plan.rows[0]["lots"] == 10
    assert plan.rows[0]["quantity"] == 100
    assert plan.invested == pytest.approx(28_274)
    assert plan.reserve == pytest.approx(71_726)
    assert plan.invested + plan.reserve == 100_000


def test_allocation_rejects_non_green_and_does_not_force_deployment():
    plan = plan_allocation(50_000, [{"secid": "X", "status": "RED", "price": 10,
                                    "lot_size": 1, "liquidity_ok": True}])
    assert plan.rows == [] and plan.invested == 0 and plan.reserve == 50_000


def test_basic_navigation_and_advanced_groups():
    assert BASIC_LABELS == ("Сегодня", "Мой портфель", "Куда вложить пополнение", "Акции",
                            "Спросить про портфель", "Дивиденды", "Риски", "Сценарии", "Обновить данные")
    grouped = group_advanced_pages({"Качество данных": object(), "Alpha Research Status": object(),
                                    "Неизвестная диагностика": object()})
    assert tuple(grouped) == tuple(ADVANCED_GROUPS)
    assert "Качество данных" in grouped["Данные"]
    assert "Alpha Research Status" in grouped["Alpha Research"]


def test_personal_files_and_backups_are_git_ignored():
    ignored = Path(".gitignore").read_text(encoding="utf-8")
    assert "config/portfolio_positions.local.yaml" in ignored
    assert "data/local/*" in ignored


def test_recalculate_does_not_request_market_download():
    text = Path("src/moex_analytics/portfolio_research/portfolio_editor.py").read_text(encoding="utf-8")
    assert "update_data=False" in text
    assert "download_portfolio_history" not in text


def test_q_and_a_has_allocation_question():
    text = Path("src/moex_analytics/dashboard/pages/human_portfolio.py").read_text(encoding="utf-8")
    assert "Что сейчас лучше докупить на 100 тысяч?" in text


def test_registry_query_accepts_known_secid():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE instruments(secid VARCHAR,name VARCHAR)")
    con.execute("INSERT INTO instruments VALUES ('SBERP','Сбербанк-п')")
    from moex_analytics.portfolio_research.portfolio_editor import instrument_registry

    assert instrument_registry(con) == {"SBERP": "Сбербанк-п"}
