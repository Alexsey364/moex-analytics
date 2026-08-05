"""Official Bank of Russia source catalogue."""

SOURCES = [
    {
        "name": "CBR bank sector review",
        "url": "https://www.cbr.ru/statistics/bank_sector/review/",
        "use": "sector context",
    },
    {
        "name": "CBR form 101",
        "url": "https://cbr.ru/banking_sector/credit/coinfo/f806/1904/?regnum=1481",
        "use": "SBER RAS balance",
    },
    {
        "name": "CBR form 102",
        "url": "https://cbr.ru/banking_sector/credit/coinfo/f807/1904/?regnum=1481",
        "use": "SBER RAS income",
    },
]


def discover() -> list[dict]:
    return SOURCES.copy()
