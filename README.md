# moex-analytics

Исследовательская платформа для воспроизводимого системного анализа российского
фондового рынка на основе официального MOEX ISS API и локального DuckDB.

> Статус: этап 2 — DuckDB, загрузка дневной истории и контроль качества.

## Принципы

- исходные ответы сохраняются отдельно от обработанных данных;
- параметры находятся в YAML, а не скрыты в коде;
- сомнительные данные не исправляются молча;
- исторические расчёты не используют информацию из будущего;
- исторический результат не гарантирует будущего.

## Архитектура

```text
config/                 YAML-конфигурация
data/raw/               неизменяемые ответы источников
data/processed/         производные наборы
database/               локальная DuckDB (не хранится в Git)
reports/                отчёты (не хранятся в Git)
src/moex_analytics/     Python-пакет
tests/                  тесты
.github/workflows/      CI
```

Планируемый поток: `MOEX ISS -> raw -> проверка -> DuckDB -> аналитика -> отчёт`.
Конфигурация ничего не скачивает; клиент источника ничего не рассчитывает; слой
хранения отвечает за идемпотентную запись; аналитика читает нормализованные данные.

## Установка

Требуется Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Проверки

```bash
ruff check .
ruff format --check .
pytest --cov=moex_analytics
```

Их же выполняет GitHub Actions.

## Конфигурация

`config/settings.yaml` содержит пути и сетевые настройки. `config/instruments.yaml`
задаёт IMOEX, SBER, LKOH и GAZP. Их `engine`, `market` и `board` будут записаны только
после проверки через официальный ISS API на этапе загрузчика.

## Команды CLI

```bash
python -m moex_analytics.cli discover
python -m moex_analytics.cli init-db
python -m moex_analytics.cli download --ticker SBER --from-date 2024-01-01 --to-date 2024-01-31
python -m moex_analytics.cli download-all --from-date 2024-01-01 --to-date 2024-01-31
python -m moex_analytics.cli quality-check
python -m moex_analytics.cli status
```

`discover` получает параметры из официального MOEX ISS. Каждый JSON-ответ истории
сохраняется в `data/raw`, после чего нормализованные строки записываются в DuckDB.

## База и инкрементальная загрузка

- `instruments` — проверенный справочник инструментов;
- `daily_prices` — дневные OHLC, средневзвешенная цена, объёмы и сделки;
- `load_log` — журнал попыток с числом полученных и вставленных строк;
- `data_quality_issues` — найденные проблемы без молчаливых исправлений.

Уникальный ключ цены: `(trade_date, secid, board)`. Без `--from-date` загрузчик
начинает со следующего календарного дня после последней даты в DuckDB, а для пустого
ряда — с `history_from` из YAML. Повторный диапазон не создаёт дубликаты.

## Контроль качества

Проверяются дубликаты, отрицательные цены и объёмы, `high < low`, выход `open` или
`close` из диапазона, а также обязательные пропуски. Записи не изменяются автоматически.

## Пример полного запуска

```bash
python -m moex_analytics.cli init-db
python -m moex_analytics.cli discover
python -m moex_analytics.cli download-all --from-date 2024-01-01 --to-date 2024-01-31
python -m moex_analytics.cli quality-check
python -m moex_analytics.cli status
```

## Ограничения этапа 2

Ряды акций начинаются на текущей первичной доске TQBR; прежние доски автоматически
не склеиваются. Для IMOEX используется отдельный рынок `index`. Доступность зависит
от MOEX ISS. Факторы, сигналы и бэктест пока отсутствуют.

Это исследовательское ПО, а не инвестиционная рекомендация или торговый робот.
