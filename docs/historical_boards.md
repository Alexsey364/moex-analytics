# Historical boards audit

Official source: https://iss.moex.com/iss/securities/{SECID}.json, checked
2026-08-04. Discovery responses are saved by discover-history.

| Instrument | Included chain | Available range |
|---|---|---|
| IMOEX | SNDX | 1997-09-22 onward |
| SBER | EQBR -> TQBR | 2011-11-21 onward |
| LKOH | EQBR -> TQBR | 2003-08-20 onward |
| GAZP | EQNE -> TQNE -> TQBS -> TQBR | 2006-01-23 onward |

TQBR starts on 2013-03-25 for SBER/LKOH after the main market moved to T+.
GAZP passed through TQNE and TQBS before TQBR on 2014-06-09.

SECID stays unchanged and MOEX identifies these rows as the same security.
Board overlaps are retained in daily_prices; canonical selection uses explicit
priority and records every conflict.

SMAL (odd lots), EQDP (blocks), SPEQ (deliveries), EQCC and special regimes are
excluded because their prices and volumes are not comparable main-market substitutes.
