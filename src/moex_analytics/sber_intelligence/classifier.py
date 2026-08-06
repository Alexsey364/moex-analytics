"""Rule-based event classification."""

RULES = {
    "RAS annual income": ("financial", "ras_results", "document_type:RAS annual income"),
    "RAS annual balance": ("financial", "ras_balance", "document_type:RAS annual balance"),
    "dividend_registry": ("dividend", "record_date", "MOEX ISS dividend registry date"),
    "key_rate": ("regulatory", "key_rate", "CBR series cbr_key_rate"),
}


def classify(document_type: str) -> tuple[str, str, str]:
    return RULES.get(document_type, ("other", "unclassified", "no strict rule"))
