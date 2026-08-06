# Этап 11B: Deep Historical Backfill and Common Sample

Финальная production-модель направления SBER на этом этапе не создаётся. Отсутствующие блоки не импутируются до даты их фактического появления.

## Фактический результат

| Блок | Результат | Статус |
|---|---:|---|
| ZCYC Банка России | 40 980 строк, 3 415 полных кривых, 2013-01-08–2026-08-05, 12 сроков | validated historical archive |
| Архив SBRF | 20 контрактов, 16 inactive/expired | experimental; specification scale требует review |
| Raw futures | 4 185 строк, 2021-09-02–2026-08-05 | experimental |
| Continuous | 1 233 даты × 4 roll rules | experimental |
| Roll history | 16 roll на правило | PIT-safe selection; production rule не выбран |
| Basis | не рассчитан | blocked до подтверждения multiplier/price scale/underlying units |
| Dynamic universe | 75 999 membership rows, 40 бумаг, 3 339 дней | blocked_by_data_quality: EOD archive пока содержит current-40 |
| Survivorship diagnostic | 3 339 дней; mean breadth diff -0.000205, max abs 0.3333 | численно рассчитан, но не является полной оценкой без исчезнувших бумаг |
| Historical financial sector | 0 PIT constituent rows | insufficient_history: бесплатный ISS не содержит исторической секторной классификации |
| Intraday | 52 000 строк, 4 series | experimental; 1m и 60m, bounded archive |
| IFRS review | 45 metric-review records, 0 validated | requires_manual_review: отсутствуют точные page/table/source-line/value |
| Options history pilot | 40 контрактов, 1 440 history rows | experimental; snapshot отделён от history |
| Common sample | 20 154 строк, 3 359 дат × 6 горизонтов | built, missing blocks not imputed |
| Coverage tiers | 30 horizon/tier records | Tier 1–5 insufficient: historical financial sector unavailable |
| Deep ablation | 30 common-sample comparisons | insufficient_common_sample, metrics intentionally not fabricated |

## Методология

- ZCYC загружается с официальной страницы CBR двухлетними чанками. Каждая дата содержит 0.25/0.5/0.75/1/2/3/5/7/10/15/20/30Y, `available_from=19:00 Europe/Moscow`, parser version и revision hash.
- Dynamic universe использует `shift(1)` перед trailing turnover и trade-day count. Сегодняшний оборот не влияет на сегодняшнее включение.
- Expiration архивных SBRF восстанавливается только из официального SHORTNAME (`SBRF-9.21`) по квартальному календарному правилу; source contract хранится в каждой continuous строке.
- Roll сравнивается по expiry, volume, OI и combined. Back- и ratio-adjustment не уничтожают raw series.
- Basis блокируется, пока спецификация масштаба не подтверждена.
- Options reference snapshot и history-by-SECID хранятся отдельно.
- IFRS candidate нельзя валидировать без document/page/table/line/raw fragment/unit и confidence >= 0.9.
- Common sample содержит отдельные availability/PIT/quality/missingness JSON и не делает backfill отсутствия.

## Почему этап не разрешает финальную модель

Главный блокер — отсутствие официального point-in-time состава финансового сектора и EOD истории исчезнувших бумаг в локальном архиве. Tier 1 требует technical + historical breadth + financial sector + ZCYC, поэтому common rows Tier 1–5 равны нулю. Нужен отдельный официальный constituent archive или лицензированный продукт MOEX, а также EOD backfill бумаг, исчезнувших до текущей даты.

Исторический order log/стакан и полный архив опционов могут требовать договор/платный продукт MOEX. Синтетические значения не создавались.
