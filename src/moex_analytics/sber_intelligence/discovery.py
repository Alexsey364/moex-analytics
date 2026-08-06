"""Source and document discovery from official stores already persisted by the project."""

from .sources import SOURCES


def discover(con) -> dict:
    for s in SOURCES:
        con.execute(
            """INSERT INTO sber_information_sources VALUES (?,?,?,?,?,?,?,?,?,?,current_timestamp)
        ON CONFLICT(source_id) DO UPDATE SET last_successful_check=now()""",
            [
                s["source_id"],
                s["name"],
                s["domain"],
                s["source_type"],
                s["trust"],
                s["timezone"],
                s["archive"],
                s["method"],
                s["limitations"],
                s["license"],
            ],
        )
    return {"sources": len(SOURCES), "official": len(SOURCES)}
