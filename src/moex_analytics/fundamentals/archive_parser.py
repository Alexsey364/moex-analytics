"""Safe parsers for official CBR HTML tables."""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from .normalization import normalize

LABELS = {
    "Прибыль (убыток) за отчетный период": "net_profit",
    "Прибыль (убыток) до налогообложения": "profit_before_tax",
    "Чистые процентные доходы (отрицательная процентная маржа)": "net_interest_income",
    "Операционные расходы": "operating_expenses",
    "Чистые доходы (расходы)": "operating_income",
    "Возмещение (расход) по налогу на прибыль": "tax_expense",
    "Всего активов": "total_assets",
    "Всего источников собственных средств": "total_equity",
    "Средства клиентов, оцениваемые по амортизированной стоимости": "customer_accounts",
}


def parse_number(value) -> float:
    text = re.sub(r"[^0-9,()\-]", "", str(value)).replace(",", ".")
    if not text or text.lower() == "nan":
        raise ValueError("empty numeric cell")
    negative = text.startswith("(") and text.endswith(")")
    number = float(text.strip("()"))
    return -number if negative else number


def parse_cbr_html(path: Path) -> list[dict]:
    html = path.read_text(encoding="utf-8")
    output = []
    for table_index, table in enumerate(re.findall(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)):
        for table_row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S | re.I):
            cells = [
                unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", table_row, re.S | re.I)
            ]
            if len(cells) < 4:
                continue
            label = re.sub(r"\s+", " ", cells[1]).strip()
            if label not in LABELS:
                continue
            try:
                raw = parse_number(cells[3])
            except ValueError:
                continue
            normalized, unit, rule = normalize(raw, "тыс. руб.")
            output.append(
                {
                    "metric_id": LABELS[label],
                    "raw_value": raw,
                    "raw_unit": "тыс. руб.",
                    "normalized_value": normalized,
                    "normalized_unit": unit,
                    "normalization_rule": rule,
                    "source_table": f"table_{table_index}",
                    "source_note": label,
                }
            )
    return output


def parse_pdf_text(text: str, required_headers: tuple[str, ...]) -> tuple[list[dict], str]:
    """PDF is never accepted unless every expected table header is present."""
    if not text.strip() or any(header not in text for header in required_headers):
        return [], "requires_manual_review"
    return [], "requires_manual_review"


def parse_html_text(text: str) -> list[dict]:
    """Strict parser helper for deterministic tests."""
    import tempfile

    path = Path(tempfile.gettempdir()) / "moex-analytics-cbr-parser.html"
    path.write_text(text, encoding="utf-8")
    return parse_cbr_html(path)
