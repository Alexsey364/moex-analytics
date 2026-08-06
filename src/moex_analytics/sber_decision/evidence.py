"""Evidence extraction from already validated point-in-time stores."""

import json

from .models import Evidence


def collect(con, as_of):
    state = con.execute(
        "SELECT * FROM sber_daily_fundamental_state WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    cols = [d[0] for d in con.description] if state else []
    state = dict(zip(cols, state, strict=True)) if state else {}
    feature = con.execute(
        "SELECT features_json FROM daily_features WHERE canonical_secid='SBER' AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    f = json.loads(feature[0]) if feature and feature[0] else {}
    regime = con.execute(
        "SELECT regime FROM market_regimes WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1", [as_of]
    ).fetchone()
    confidence = state.get("data_confidence") or 0
    roe = state.get("roe_ttm") or 0
    pe = state.get("pe_trailing") or 0
    blocks = [
        Evidence(
            "business_quality",
            max(-1, min(1, (roe - 0.12) / 0.15)),
            confidence,
            "available",
            ("ROE основан на validated РСБУ",),
            (),
            {"roe": roe},
            as_of,
        ),
        Evidence(
            "valuation",
            0.35 if 0 < pe < 6 else (-0.3 if pe > 10 else 0),
            state.get("valuation_confidence") or 0,
            "available",
            ("низкий trailing P/E",) if 0 < pe < 6 else (),
            (),
            {"pe": pe},
            as_of,
        ),
        Evidence(
            "dividend",
            0.25 if (state.get("dividend_yield_expected") or 0) > 0.08 else 0,
            confidence * 0.8,
            "available",
            (),
            (),
            {"yield": state.get("dividend_yield_expected")},
            as_of,
        ),
        Evidence(
            "technical",
            float(f.get("rsi_14", 50) - 50) / 50 if f else 0,
            55 if f else 0,
            "available" if f else "missing",
            (),
            (),
            f,
            as_of,
        ),
        Evidence("relative_strength", 0, 45, "experimental", (), (), {}, as_of),
        Evidence(
            "market_regime",
            -0.3 if regime and "stress" in regime[0].lower() else 0,
            60 if regime else 0,
            "available" if regime else "missing",
            (),
            (),
            {"regime": regime[0] if regime else None},
            as_of,
        ),
        Evidence("analogues", 0, 35, "limited_sample", (), ("выборка аналогов ограничена",), {}, as_of),
        Evidence("risk", -0.15, 55, "available", (), ("банковский и рыночный риск",), {}, as_of),
        Evidence(
            "data_quality", 0, confidence, "available", (), ("validated МСФО отсутствует",), state, as_of
        ),
        Evidence(
            "event_information",
            0,
            0,
            "experimental_weight_zero",
            (),
            ("историческая полезность информационного слоя ещё не подтверждена",),
            {},
            as_of,
        ),
        Evidence("macro", 0, 0, "rejected_excluded", (), ("добавочная ценность не доказана",), {}, as_of),
    ]
    return blocks, state
