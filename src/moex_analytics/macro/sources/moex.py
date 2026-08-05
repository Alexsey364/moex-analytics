"""MOEX ISS adapters for currency, bond and sector index history."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from ...moex_client import MoexClient
from ..models import Observation, SeriesDefinition

MOSCOW = ZoneInfo("Europe/Moscow")
INSTRUMENTS = {
    "moex_cny_rub": ("CNYRUB_TOM", "currency", "selt", "CETS", "Market CNY/RUB"),
    "moex_usd_rub": ("USDRUB_TOM", "currency", "selt", "CETS", "Market USD/RUB"),
    "moex_rgbi": ("RGBI", "stock", "index", "SNDX", "Government bond price index"),
    "moex_ofz_3y": ("RUGBITR3Y", "stock", "index", "RTSI", "Government bonds 1-3Y TR"),
    "moex_ofz_5y": ("RUGBITR5Y", "stock", "index", "RTSI", "Government bonds 3-5Y TR"),
    "moex_ofz_10y": ("RUGBITR10Y", "stock", "index", "RTSI", "Government bonds 5-10Y TR"),
    "moex_finance": ("MOEXFN", "stock", "index", "SNDX", "Financial sector index"),
    "moex_oil_gas": ("MOEXOG", "stock", "index", "SNDX", "Oil and gas sector index"),
    "moex_metals": ("MOEXMM", "stock", "index", "SNDX", "Metals and mining index"),
    "moex_consumer": ("MOEXCN", "stock", "index", "SNDX", "Consumer sector index"),
    "moex_transport": ("MOEXTN", "stock", "index", "SNDX", "Transport sector index"),
    "moex_power": ("MOEXEU", "stock", "index", "SNDX", "Electric utilities index"),
}


def definitions() -> list[SeriesDefinition]:
    return [
        SeriesDefinition(
            sid,
            name,
            "index points" if engine == "stock" else "RUB",
            "trading daily",
            "MOEX ISS",
            f"https://iss.moex.com/iss/history/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json",
            None,
            "Available after the relevant MOEX session close",
            "MOEX history endpoint, no vintage archive",
            True,
            "Official and exchange FX series are not spliced",
        )
        for sid, (secid, engine, market, board, name) in INSTRUMENTS.items()
    ]


def normalize_history(series_id: str, payload: dict) -> list[Observation]:
    block = payload["history"]
    rows = [dict(zip(block["columns"], values, strict=True)) for values in block["data"]]
    result = []
    for row in rows:
        if row.get("CLOSE") is None or row.get("TRADEDATE") is None:
            continue
        observed = datetime.strptime(row["TRADEDATE"], "%Y-%m-%d").date()
        result.append(
            Observation(
                series_id,
                observed,
                observed,
                datetime.combine(observed, time(18, 50), MOSCOW),
                float(row["CLOSE"]),
                "iss-history",
                "https://iss.moex.com/iss/",
            )
        )
    return result


def download(
    series_id: str, date_from: str, date_to: str, client: MoexClient | None = None
) -> list[Observation]:
    secid, engine, market, board, _ = INSTRUMENTS[series_id]
    api = client or MoexClient()
    instrument = {"source_secid": secid, "engine": engine, "market": market, "board": board}
    result = []
    for payload, _, _ in api.history_pages(instrument, date_from, date_to):
        result.extend(normalize_history(series_id, payload))
    return result
