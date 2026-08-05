"""Persistence helpers for decision outputs."""

import json


def save_evidence(con, run_id, blocks, version):
    for b in blocks:
        con.execute(
            "INSERT INTO sber_decision_evidence VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            [
                run_id,
                b.block_id,
                b.score,
                b.confidence,
                b.status,
                json.dumps(b.positive, ensure_ascii=False),
                json.dumps(b.negative, ensure_ascii=False),
                json.dumps(b.data, ensure_ascii=False, default=str),
                b.data_date,
                version,
            ],
        )
