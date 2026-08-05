"""Parse, validate and build the real RAS history."""

from __future__ import annotations

import duckdb

from .archive_parser import parse_cbr_html


def parse_downloaded(con: duckdb.DuckDBPyConnection) -> dict:
    parsed = manual = metrics = 0
    docs = con.execute("""SELECT document_id,local_path,accounting_standard,period_start,period_end,
      publication_date,available_from,revision_id FROM fundamental_documents
      WHERE processing_status='downloaded'""").fetchall()
    for doc_id, path, standard, start, end, published, available, revision in docs:
        rows = parse_cbr_html(__import__("pathlib").Path(path))
        if not rows:
            con.execute(
                "UPDATE fundamental_documents SET processing_status='requires_manual_review',validation_status='requires_manual_review' WHERE document_id=?",
                [doc_id],
            )
            manual += 1
            continue
        for row in rows:
            con.execute(
                """INSERT INTO fundamental_metric_values VALUES
                (?,'SBER',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'parsed',current_timestamp)
              ON CONFLICT(document_id,metric_id,revision_id) DO NOTHING""",
                [
                    doc_id,
                    row["metric_id"],
                    row["raw_value"],
                    row["raw_unit"],
                    row["normalized_value"],
                    row["normalized_unit"],
                    row["normalization_rule"],
                    standard,
                    start,
                    end,
                    published,
                    available,
                    None,
                    row["source_table"],
                    row["source_note"],
                    revision,
                ],
            )
            metrics += 1
        con.execute(
            "UPDATE fundamental_documents SET processing_status='parsed' WHERE document_id=?", [doc_id]
        )
        parsed += 1
    return {"parsed": parsed, "manual_review": manual, "metrics": metrics}


def validate(con: duckdb.DuckDBPyConnection) -> dict:
    validated = rejected = 0
    docs = con.execute(
        """SELECT document_id,document_type,period_end,publication_date,title
        FROM fundamental_documents WHERE processing_status NOT IN
        ('discovered','downloaded','requires_manual_review')"""
    ).fetchall()
    for doc_id, kind, period_end, published, title in docs:
        values = dict(
            con.execute(
                "SELECT metric_id,normalized_value FROM fundamental_metric_values WHERE document_id=?",
                [doc_id],
            ).fetchall()
        )
        required = (
            {"total_assets", "total_equity"} if "balance" in kind else {"net_profit", "profit_before_tax"}
        )
        incomplete_income = "income" in kind and ("месяц" in title.lower() or "квартал" in title.lower())
        ok = (
            required.issubset(values)
            and all(values[x] > 0 for x in required)
            and published >= period_end
            and not incomplete_income
        )
        status = "validated" if ok else "requires_manual_review" if incomplete_income else "rejected"
        con.execute(
            "UPDATE fundamental_documents SET processing_status=?,validation_status=? WHERE document_id=?",
            [status, status, doc_id],
        )
        con.execute(
            "UPDATE fundamental_metric_values SET quality_status=? WHERE document_id=?", [status, doc_id]
        )
        validated += int(ok)
        rejected += int(not ok)
    return {"validated": validated, "rejected": rejected}


def import_validated(con: duckdb.DuckDBPyConnection) -> int:
    before = con.execute("SELECT count(*) FROM fundamental_observations").fetchone()[0]
    con.execute("DELETE FROM fundamental_observations WHERE secid='SBER' AND accounting_standard='RAS'")
    rows = con.execute("""SELECT v.metric_id,v.period_start,v.period_end,d.document_type,
      v.accounting_standard,v.publication_date,v.available_from,v.normalized_value,v.normalized_unit,
      'Bank of Russia',d.source_url,v.revision_id FROM fundamental_metric_values v
      JOIN fundamental_documents d USING(document_id) WHERE v.quality_status='validated'""").fetchall()
    for row in rows:
        con.execute(
            """INSERT INTO fundamental_observations VALUES ('SBER',?,?,?,?,?,?,?,?,?,?,?,?,current_timestamp)
          ON CONFLICT(secid,metric_id,period_end,accounting_standard,revision_id) DO NOTHING""",
            list(row),
        )
    after = con.execute("SELECT count(*) FROM fundamental_observations").fetchone()[0]
    return max(0, int(after - before))


def install_regimes(con: duckdb.DuckDBPyConnection) -> None:
    regimes = [
        (
            "ras-early",
            "RAS published forms before stable archive",
            "cbr-pre-v1",
            None,
            __import__("datetime").date(2017, 12, 31),
            "requires_manual_review",
            "No machine-stable SBER archive confirmed",
        ),
        (
            "ras-stable",
            "CBR RAS published forms",
            "cbr-html-v1",
            __import__("datetime").date(2018, 1, 1),
            None,
            "fully_comparable",
            "Stable form 806/807 annual HTML; 2021 archive gap retained",
        ),
    ]
    for row in regimes:
        con.execute(
            "INSERT INTO fundamental_accounting_regimes VALUES (?,?,?,?,?,?,?) ON CONFLICT(regime_id) DO NOTHING",
            row,
        )
