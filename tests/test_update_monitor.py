from datetime import UTC, datetime, timedelta

from moex_analytics import update_monitor


def test_live_progress_unknown_total_eta_and_receipt(tmp_path):
    path = tmp_path / "status.json"
    state = update_monitor.start("run", "quick", path)
    assert update_monitor.eta_seconds(state) is None
    update_monitor.progress(state, dataset="prices", stage="Prices", source="MOEX ISS",
                            status="completed", requests=2, rows=10, duration=1.5, path=path)
    update_monitor.progress(state, dataset="macro", stage="Macro", source="CBR",
                            status="smart_skip", path=path)
    assert update_monitor.load(path)["rows_inserted"] == 10
    assert update_monitor.eta_seconds(state) is not None
    update_monitor.finish(state, "completed", path)
    assert update_monitor.load(path)["status"] == "completed"


def test_heartbeat_active_slow_and_stalled():
    now = datetime.now(UTC)
    state = {"last_progress_at": now.isoformat()}
    assert update_monitor.health(state, now) == "ACTIVE"
    state["last_progress_at"] = (now - timedelta(seconds=40)).isoformat()
    assert update_monitor.health(state, now) == "SLOW"
    state["last_progress_at"] = (now - timedelta(seconds=100)).isoformat()
    assert update_monitor.health(state, now) == "STALLED"


def test_safe_cancel_flag_and_resume_clear(tmp_path):
    flag = tmp_path / "cancel.flag"
    update_monitor.request_cancel(flag)
    assert update_monitor.cancel_requested(flag)
    update_monitor.clear_cancel(flag)
    assert not update_monitor.cancel_requested(flag)


def test_crash_recovery_marks_dead_process_interrupted(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    state = update_monitor.start("run", "quick", path)
    state["pid"] = 999999
    update_monitor._write(state, path)
    monkeypatch.setattr(update_monitor, "process_alive", lambda _pid: False)
    assert update_monitor.recover_interrupted(path)["status"] == "interrupted"


def test_events_are_bounded_and_never_store_response_bodies(tmp_path):
    path = tmp_path / "status.json"
    state = update_monitor.start("run", "quick", path)
    for _ in range(60):
        update_monitor.progress(state, dataset="prices", stage="candles", source="MOEX ISS",
                                status="running", path=path)
    stored = update_monitor.load(path)
    assert len(stored["events"]) == 50
    assert "candles" in stored["events"][-1]["message"]
