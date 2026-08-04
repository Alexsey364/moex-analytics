import requests

from moex_analytics.moex_client import MoexClient


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.url = "https://iss.moex.com/test"

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class Session:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.responses.pop(0)


def payload(rows, index=0, total=None):
    columns = [
        "TRADEDATE",
        "OPEN",
        "HIGH",
        "LOW",
        "CLOSE",
        "WAPRICE",
        "VOLUME",
        "VALUE",
        "NUMTRADES",
    ]
    return {
        "history": {"columns": columns, "data": rows},
        "history.cursor": {
            "columns": ["INDEX", "TOTAL", "PAGESIZE"],
            "data": [[index, len(rows) if total is None else total, 1]],
        },
    }


def test_normalize_history():
    rows = [["2024-01-10", 1, 3, 0.5, 2, 1.5, 10, 20, 5]]
    normalized = MoexClient.normalize_history(payload(rows), "SBER", "TQBR", "url")
    assert normalized[0]["weighted_average_price"] == 1.5
    assert normalized[0]["number_of_trades"] == 5


def test_pagination(monkeypatch, tmp_path):
    session = Session([Response(payload([[1] * 9], 0, 2)), Response(payload([[2] * 9], 1, 2))])
    client = MoexClient(session=session, sleep=lambda _: None)
    client.raw_dir = tmp_path
    instrument = {"secid": "SBER", "engine": "stock", "market": "shares", "board": "TQBR"}
    assert len(list(client.history_pages(instrument, "2024-01-01", "2024-01-31"))) == 2
    assert session.calls[1][1]["start"] == 1


def test_retry_after_temporary_error():
    session = Session([Response({}, 503), Response({"ok": True})])
    sleeps = []
    client = MoexClient(session=session, sleep=sleeps.append)
    assert client.get_json("test") == {"ok": True}
    assert len(session.calls) == 2
    assert sleeps == [1.0]
