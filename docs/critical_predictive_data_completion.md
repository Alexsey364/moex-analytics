# Critical Predictive Data Completion for SBER (этап 11A)

Этап добавляет официальный data-foundation перед построением модели направления SBER. Production-модель на этом этапе не создаётся.

## Архитектура

`moex_analytics.critical_data.schema` содержит отдельные таблицы DuckDB: историческая вселенная и PIT membership, ZCYC, raw/continuous futures с provenance и roll history, IFRS discovery/review, options audit, intraday candles/features, source catalog, QC, readiness и ablation.

`moex_analytics.critical_data.core` разделяет чистые функции (PIT liquidity с обязательным `shift(1)`, board de-duplication, curve interpolation, roll/back-adjustment, basis, arbitrage bounds, sessions, common sample) и официальные загрузчики MOEX ISS/Bank of Russia/Sber IR. Ни один недоступный ряд не заменяется синтетическим.

Dashboard показывает 11 отдельных представлений; CLI предоставляет команды из задания и полную команду `complete-sber-critical-data`.

## Проверенные официальные источники и фактический результат

| Блок | Endpoint | Результат | Решение |
|---|---|---|---|
| Historical shares | MOEX ISS `securities.json?group_by=group&group_by_filter=stock_shares` | 3 365 карточек: 1 035 active, 2 330 inactive | `experimental`: discovery шире current TQBR, но lifecycle/price membership ещё требует backfill |
| ZCYC | CBR `/hd_base/zcyc_params/zcyc/?DateTo=...` | 12 реальных точек 0.25–30Y на 2026-08-05 | `experimental`: validated slice, недостаточная история |
| SBER futures | MOEX ISS FORTS, `ASSETCODE=SBRF` | 4 контракта, 554 daily rows, 233 continuous rows | `experimental`: source contract сохранён; архивные expired contracts и rolls отсутствуют |
| IFRS | Sber IR и MOEX issuer page | 3 audit records, 1 доступный HTML, 15 review fields | `requires_manual_review`: нет metric без PDF page/table validation |
| Options | MOEX ISS options snapshot | 4 648 релевантных контрактов | `experimental` только snapshot; глубокая история/стакан требуют отдельного продукта MOEX |
| Intraday | MOEX ISS candles | 52 000 rows; SBER 1m начинается 2011-12-15; recent SBER/IMOEX 1m и 60m; 5m/15m вернули 0 | `experimental`: bounded audit, не полный backfill |
| Ablation | local real-data registry | 54 rows, horizons 1/5/20/60/120/250 | `insufficient_common_sample`; production status не присвоен |

## Point-in-time правила

- universe lifecycle ограничивает дату включения;
- порог ликвидности использует только `shift(1).rolling(...)`;
- пересечение boards дедуплицируется с приоритетом primary board;
- ZCYC доступна не ранее 19:00 MSK даты публикации;
- futures continuous хранит контракт-источник каждой строки;
- IFRS запрещено использовать без publication timestamp и source page;
- candles не используются для реконструкции bid/ask или стакана.

## Что осталось до production-ready

1. Обогатить 3 365 карточек boards/lifecycles и загрузить EOD историю исчезнувших бумаг; затем материализовать PIT membership и сравнить survivorship bias.
2. Получить официальные секторные метаданные по периодам и построить financial-sector series.
3. Выполнить исторический backfill ZCYC.
4. Найти архив завершённых SBRF contracts и подтвердить rolls тремя правилами; добавить spot basis с корректным масштабом контракта.
5. Скачать прямые IFRS PDF/XLSX, заполнить page/table/value и провести manual validation.
6. Решить вопрос лицензии на глубокую историю опционов/order log.
7. Дозагрузить intraday инкрементально и выполнить real common-sample ablation.

До закрытия этих пунктов финальная production-модель направления SBER запрещена.
