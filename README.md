# moex-analytics

Этап 7 добавляет point-in-time фундаментальный слой только для SBER и прозрачную сценарную оценку
на 12 месяцев. Источники, ограничения и контролируемый импорт описаны в
`docs/sber_fundamental_sources.md`; методология — в `docs/sber_valuation_methodology.md`.

Исследовательская платформа для воспроизводимого системного анализа российского
фондового рынка на основе официального MOEX ISS API и локального DuckDB.

> Статус: этап 3 — исторические доски, канонические ряды, дивиденды и доходности.

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
python -m moex_analytics.cli discover-history
python -m moex_analytics.cli download-history --ticker SBER
python -m moex_analytics.cli download-history-all
python -m moex_analytics.cli build-canonical
python -m moex_analytics.cli download-dividends
python -m moex_analytics.cli calculate-returns
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

## Историческая методология

Подробности находятся в docs/historical_boards.md, docs/data_methodology.md и
docs/dividend_methodology.md. Исходные доски не склеиваются в daily_prices.
Канонический ряд выбирает строку по явному приоритету и регистрирует пересечения.

## Ограничения этапа 3

ISS dividends не сообщает даты объявления и выплаты, поэтому они остаются NULL.
Исторический total-return использует фактические выплаты и не является point-in-time
рядом. Конфликты досок не исправляются автоматически. Бэктест отсутствует.

Это исследовательское ПО, а не инвестиционная рекомендация или торговый робот.

## Запуск браузерного приложения

Приложение работает только локально и слушает `http://localhost:8501`.

```powershell
.\.venv\Scripts\Activate.ps1
python -m moex_analytics.cli dashboard
```

В Windows также можно дважды щёлкнуть `start_dashboard.bat`. Скрипт проверит `.venv`
и наличие базы `database/market.duckdb`; если базы нет, панель предложит создать её.
Пакеты автоматически не устанавливаются.

Для остановки нажмите `Ctrl+C` в окне запуска. Если порт 8501 занят, завершите другой
Streamlit-процесс или запустите вручную с другим `--server.port`. Если браузер не
открылся, перейдите на `http://localhost:8501` вручную.

При отсутствии окружения выполните:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

На странице «Обновление данных» доступны отдельные операции и полное последовательное
обновление. Ошибка шага останавливает зависимые операции и показывается пользователю.

## Predictive Data Foundation

Команда python -m moex_analytics.cli update-predictive-foundation строит официальный source
catalog, загружает широкую TQBR-вселенную, рассчитывает market breadth и SBER relative state,
исследует FORTS, structural regimes, coverage и preliminary common-sample ablation. Этап не
строит финальную production-модель направления. Платные и недоступные источники не заменяются
синтетическими данными.

## Этап 11A: Critical Predictive Data Completion

Добавлен контур `moex_analytics.critical_data` для исторической вселенной, ZCYC Банка России, SBER futures, IFRS/options audit и intraday-сессий. Методология, фактическая доступность и незакрытые ограничения описаны в [docs/critical_predictive_data_completion.md](docs/critical_predictive_data_completion.md). Ни один блок автоматически не получает production-статус, production-модель направления SBER не строится.

Полный запуск: `python -m moex_analytics.cli complete-sber-critical-data`. Для больших архивов рекомендуется выполнять отдельные идемпотентные CLI-команды.

## Этап 11B: Deep Historical Backfill

Модуль `moex_analytics.deep_backfill` загружает многолетнюю ZCYC, архив SBRF, строит четыре continuous-ряда, динамическую universe-диагностику, option-history pilot, common sample и coverage tiers. Фактические результаты и блокеры описаны в [docs/deep_historical_backfill.md](docs/deep_historical_backfill.md). Финальная production-модель не создаётся.
