from moex_analytics.portfolio_research.core import _board_security


def test_lot_size_is_read_from_official_board_security_block() -> None:
    payload = {
        "securities": {
            "columns": ["SECID", "BOARDID", "LOTSIZE"],
            "data": [["SBERP", "TQBR", 10]],
        }
    }
    assert _board_security(payload)["LOTSIZE"] == 10
    assert _board_security({"securities": {"columns": [], "data": []}}) == {}
