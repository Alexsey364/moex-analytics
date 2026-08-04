"""Resilient client for the official MOEX ISS API."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from .config import PROJECT_ROOT, load_settings


class MoexError(RuntimeError):
    """A clear error raised for an unsuccessful ISS operation."""


class MoexClient:
    def __init__(self, session: requests.Session | None = None, sleep=time.sleep) -> None:
        config = load_settings()["moex_iss"]
        self.base_url = config["base_url"].rstrip("/")
        self.timeout = config["timeout_seconds"]
        self.max_retries = config["max_retries"]
        self.backoff = config["retry_backoff_seconds"]
        self.page_size = config["page_size"]
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config["user_agent"]})
        self.sleep = sleep
        self.raw_dir = PROJECT_ROOT / load_settings()["paths"]["raw_data"]

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"temporary HTTP {response.status_code}", response=response)
                response.raise_for_status()
                return response.json()
            except (
                requests.Timeout,
                requests.ConnectionError,
                requests.HTTPError,
                ValueError,
            ) as exc:
                retryable = not isinstance(exc, requests.HTTPError) or (
                    exc.response is not None and exc.response.status_code in {429, 500, 502, 503, 504}
                )
                if not retryable or attempt + 1 == self.max_retries:
                    raise MoexError(f"MOEX ISS request failed: {url}: {exc}") from exc
                self.sleep(self.backoff * (2**attempt))
        raise AssertionError("unreachable")

    def discover(self, secid: str) -> dict[str, Any]:
        payload = self.get_json(f"securities/{secid}.json", {"iss.meta": "off"})
        description = {row[0]: row[2] for row in payload["description"]["data"]}
        board_columns = payload["boards"]["columns"]
        boards = [dict(zip(board_columns, row, strict=True)) for row in payload["boards"]["data"]]
        primary = next((row for row in boards if row["is_primary"] == 1), None)
        if primary is None:
            raise MoexError(f"No primary board returned for {secid}")
        return {
            "secid": secid,
            "name": description["NAME"],
            "instrument_type": description["TYPE"],
            "engine": primary["engine"],
            "market": primary["market"],
            "board": primary["boardid"],
            "history_from": primary["history_from"],
            "history_available": primary["history_from"] is not None,
            "is_active": bool(primary["is_traded"]),
        }

    def discover_history(self, secid: str) -> list[dict[str, Any]]:
        payload = self.get_json(f"securities/{secid}.json", {"iss.meta": "off"})
        columns = payload["boards"]["columns"]
        boards = [dict(zip(columns, row, strict=True)) for row in payload["boards"]["data"]]
        result = [
            row
            for row in boards
            if row["engine"] == "stock"
            and row["market"] in {"shares", "index"}
            and row["history_from"] is not None
        ]
        self.save_raw(secid, "boards", 0, payload)
        return result

    def history_pages(
        self, instrument: dict[str, Any], date_from: str, date_to: str
    ) -> Iterator[tuple[dict[str, Any], int, str]]:
        secid = instrument.get("source_secid", instrument.get("secid"))
        if not secid:
            raise MoexError("History instrument requires secid or source_secid")
        path = (
            f"history/engines/{instrument['engine']}/markets/{instrument['market']}/"
            f"boards/{instrument['board']}/securities/{secid}.json"
        )
        start = 0
        while True:
            params = {
                "from": date_from,
                "till": date_to,
                "start": start,
                "iss.meta": "off",
                "iss.only": "history,history.cursor",
            }
            payload = self.get_json(path, params)
            source = f"{self.base_url}/{path}"
            self.save_raw(secid, "history", start, payload)
            yield payload, start, source
            cursor = payload.get("history.cursor", {})
            if not cursor.get("data"):
                break
            index, total, page_size = cursor["data"][0]
            start = index + page_size
            if start >= total:
                break

    def save_raw(self, secid: str, kind: str, page: int, payload: dict[str, Any]) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
        path = self.raw_dir / f"{secid}_{kind}_page-{page}_{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def dividends(self, secid: str) -> list[dict[str, Any]]:
        path = f"securities/{secid}/dividends.json"
        payload = self.get_json(path, {"iss.meta": "off"})
        self.save_raw(secid, "dividends", 0, payload)
        block = payload["dividends"]
        rows = [dict(zip(block["columns"], row, strict=True)) for row in block["data"]]
        source = f"{self.base_url}/{path}"
        now = datetime.now()
        return [
            {
                "canonical_secid": secid,
                "registry_close_date": row["registryclosedate"],
                "declared_date": None,
                "payment_date": None,
                "dividend_per_share": row["value"],
                "currency": row["currencyid"],
                "source": source,
                "loaded_at": now,
                "notes": "ISS supplies registry close date only; declaration/payment unavailable",
            }
            for row in rows
        ]

    @staticmethod
    def normalize_history(
        payload: dict[str, Any], secid: str, board: str, source: str
    ) -> list[dict[str, Any]]:
        block = payload["history"]
        rows = [dict(zip(block["columns"], values, strict=True)) for values in block["data"]]
        now = datetime.now()
        return [
            {
                "trade_date": row.get("TRADEDATE"),
                "secid": secid,
                "board": board,
                "open": row.get("OPEN"),
                "high": row.get("HIGH"),
                "low": row.get("LOW"),
                "close": row.get("CLOSE"),
                "weighted_average_price": row.get("WAPRICE"),
                "volume": row.get("VOLUME"),
                "value": row.get("VALUE"),
                "number_of_trades": row.get("NUMTRADES"),
                "source": source,
                "loaded_at": now,
            }
            for row in rows
        ]
