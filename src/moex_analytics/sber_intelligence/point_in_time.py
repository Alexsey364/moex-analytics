"""Point-in-time selection."""


def available_as_of(rows: list[dict], as_of) -> list[dict]:
    return [row for row in rows if row["available_from"] <= as_of]


def anchor_session(publication_hour: int | None) -> str:
    if publication_hour is None:
        return "unknown"
    if publication_hour < 10:
        return "before_open"
    if publication_hour >= 19:
        return "after_close"
    return "during_session"
