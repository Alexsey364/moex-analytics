"""Official SBER/MOEX source discovery; raw documents are never reinterpreted silently."""

from pathlib import Path

import requests

SOURCES = [
    {
        "name": "SBER investor relations",
        "url": "https://www.sberbank.com/ru/investor-relations/groupresults/ifrs",
        "use": "IFRS reports and presentations",
    },
    {
        "name": "MOEX issuer documents",
        "url": "https://www.moex.com/ru/listing/emidocs.aspx?id=484",
        "use": "issuer disclosures",
    },
    {
        "name": "MOEX SBER security",
        "url": "https://www.moex.com/ru/stocks/sber?board=rpmo",
        "use": "shares and official issuer metrics",
    },
]


def discover() -> list[dict]:
    return SOURCES.copy()


def download_document(url: str, target: Path, timeout: int = 30) -> Path:
    allowed = ("https://www.sberbank.com/", "https://www.moex.com/")
    if not url.startswith(allowed):
        raise ValueError("Only official SBER/MOEX URLs are allowed")
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "moex-analytics/0.1.0"})
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return target
