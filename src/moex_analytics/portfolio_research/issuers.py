"""Issuer-specific point-in-time source maps; no aggregator is an official source."""

from __future__ import annotations

from .interfaces import AdapterContract, FundamentalAdapter
from .schema import DDL

METRICS = {
    "X5": (
        "X5FundamentalAdapter",
        "IFRS",
        (
            "revenue",
            "lfl_sales",
            "traffic",
            "average_ticket",
            "selling_space",
            "store_count",
            "ebitda",
            "ebitda_margin",
            "net_debt",
            "fcf",
            "capex",
            "dividends",
        ),
    ),
    "SBERP": (
        "SberFundamentalAdapter",
        "IFRS",
        ("profit", "equity", "roe", "cost_of_risk", "nim", "capital_adequacy", "dividends", "pe", "pb"),
    ),
    "LKOH": (
        "LukoilFundamentalAdapter",
        "IFRS",
        ("production", "refining", "ebitda", "fcf", "capex", "net_debt", "dividends", "payout"),
    ),
    "LSNGP": (
        "LenenergoFundamentalAdapter",
        "RAS",
        (
            "revenue",
            "transmission",
            "useful_supply",
            "tariff",
            "losses",
            "capex",
            "debt",
            "ras_profit",
            "dividend_formula",
        ),
    ),
    "MTSS": (
        "MtsFundamentalAdapter",
        "IFRS",
        (
            "revenue",
            "oibda",
            "margin",
            "capex",
            "fcf",
            "net_debt_oibda",
            "subscribers",
            "dividends",
            "debt_refinancing",
        ),
    ),
    "TRNFP": (
        "TransneftFundamentalAdapter",
        "IFRS",
        (
            "transportation",
            "tariffs",
            "ebitda",
            "capex",
            "debt",
            "profit",
            "dividend_base",
            "preferred_rights",
        ),
    ),
    "TATNP": (
        "TatneftFundamentalAdapter",
        "IFRS",
        ("production", "refining", "petrochemicals", "ebitda", "fcf", "capex", "net_debt", "payout"),
    ),
    "PHOR": (
        "PhosagroFundamentalAdapter",
        "IFRS",
        (
            "production",
            "sales",
            "fertilizer_prices",
            "revenue",
            "ebitda",
            "margin",
            "fcf",
            "capex",
            "debt",
            "dividends",
        ),
    ),
    "MOEX": (
        "MoexFundamentalAdapter",
        "IFRS",
        (
            "fees",
            "interest_income",
            "client_balances",
            "trading_volumes",
            "operating_expenses",
            "net_profit",
            "payout",
            "dividend",
        ),
    ),
}


class IssuerFundamentalAdapter(FundamentalAdapter):
    secid = ""

    def __init__(self):
        adapter, standard, metrics = METRICS[self.secid]
        self.adapter_name = adapter
        self.standard = standard
        self.metrics = metrics
        self.contract = AdapterContract(
            "issuer-map-v1",
            ("issuer investor relations", "e-disclosure.ru"),
            metrics,
            ("publication_date required", "available_from>=publication_date", "standard must not be mixed"),
            "document available_from must be <= research cutoff",
            "official source coverage and validation",
            "requires_manual_review",
        )

    def validate(self, payload):
        missing = [f for f in self.contract.required_fields if f not in payload]
        return not missing, missing


class X5FundamentalAdapter(IssuerFundamentalAdapter):
    secid = "X5"


class SberFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "SBERP"


class LukoilFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "LKOH"


class LenenergoFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "LSNGP"


class MtsFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "MTSS"


class TransneftFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "TRNFP"


class TatneftFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "TATNP"


class PhosagroFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "PHOR"


class MoexFundamentalAdapter(IssuerFundamentalAdapter):
    secid = "MOEX"


ADAPTERS = (
    X5FundamentalAdapter,
    SberFundamentalAdapter,
    LukoilFundamentalAdapter,
    LenenergoFundamentalAdapter,
    MtsFundamentalAdapter,
    TransneftFundamentalAdapter,
    TatneftFundamentalAdapter,
    PhosagroFundamentalAdapter,
    MoexFundamentalAdapter,
)


def discover_issuer_fundamentals(con):  # pragma: no cover - DuckDB integration
    con.execute(DDL)
    rows = 0
    for cls in ADAPTERS:
        a = cls()
        for metric in a.metrics:
            con.execute(
                "INSERT OR REPLACE INTO issuer_source_maps VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    a.secid,
                    a.adapter_name,
                    metric,
                    a.standard,
                    "issuer investor relations / official disclosure",
                    "official_primary",
                    "document publication timestamp",
                    "publication timestamp plus market-session cutoff",
                    "discoverable",
                    "schema mapping only; parsing/validation is a later evidence task",
                ],
            )
            rows += 1
    return {"issuers": len(ADAPTERS), "metrics": rows, "status": "source_discovery_and_schema_mapping"}
