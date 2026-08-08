# Historical Data Completion & Coverage Audit

Этап 20 вводит единый контур учёта исторических данных для девяти issuer groups портфеля. Он не меняет production-модели и не создаёт ретроспективные прогнозы.

## Принципы

- `historical_data_coverage` содержит одну запись на issuer group и family, включая происхождение, доступ, лицензию, диапазон, полноту, PIT-статус, survivorship safety, blocker и рекомендуемое действие.
- Data Priority Score является прозрачной порядковой оценкой. Компоненты заданы шкалой 0–3; итог используется только для категорий `critical`, `high`, `medium`, `low`, `paid_optional`.
- FIVE и X5 не объединяются без датированного подтверждённого corporate-action mapping.
- Текущая sector classification не протягивается в прошлое. Каждый интервал имеет `valid_from`/`valid_to` и источник.
- Basis выключен до проверки spot/futures scale, multiplier, lot, currency и expiration.
- Платные options, consensus, flows и order-book history только каталогизируются. Покупка и автоматическое копирование не выполняются.
- Research ablation принимает только одинаковую датированную выборку, walk-forward/OOS evidence и не повышает production-модель.

## Команды

Полный локальный аудит:

```powershell
python -m moex_analytics.cli complete-historical-data-audit
```

Доступны отдельные команды: `audit-historical-data-coverage`, `backfill-issuer-fundamentals`, `backfill-historical-universe`, `backfill-sector-history`, `backfill-external-factors`, `backfill-futures`, `audit-options-history`, `audit-corporate-actions`, `audit-dividends`, `calculate-pit-integrity`, `run-data-value-ablation`, `historical-data-status`.

Термин *backfill* здесь означает только импорт официально доступных, лицензируемых и проверяемых наблюдений. Если источник или PIT timestamp не доказан, команда сохраняет пробел и blocker, а не синтетическое значение.

## Retention

Сохраняются provenance, document hashes, revisions и исходные подтверждённые материалы. Кэш можно дедуплицировать, но валидированные raw-источники не удаляются автоматически. DuckDB, raw/processed data, локальный портфель и персональные отчёты остаются вне Git.
