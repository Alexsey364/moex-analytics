# Аудит макроэкономической модели

Команда `moex-analytics audit-macro-model` выполняет воспроизводимый аудит версии
`macro-audit-v2`. Основной результат — сравнение на общей выборке дат и целей.
Own-available sample сохраняется только как диагностическое дополнение.

## Защита от утечек

- импутация, winsorization и scaler обучаются внутри train каждого временного fold;
- test преобразуется параметрами train;
- target не входит в преобразование;
- разбиение expanding-window не перемешивается;
- дата выхода проверяется по календарю торговых сессий инструмента;
- дата исходного макронаблюдения не может быть позже торговой даты.

## Сохраняемые результаты

- `macro_data_audit` — заполненность, возраст, пропуски, выбросы, разрывы и revisions;
- `macro_matrix_audit` — размерность, missingness, дисперсия, корреляции и condition number;
- `macro_ablation_results` — common/own sample, блоки, robust transforms, регуляризация,
  permutation/noise sanity checks, paired bootstrap и Diebold–Mariano statistic;
- `macro_coefficient_audit` — коэффициенты и устойчивость знака по fold;
- `macro_regime_audit` — ошибки по режимам IMOEX;
- `macro_feature_audit` — решение по каждому блоку.

Подбор Elastic Net выполняется только во вложенном expanding-window CV на train.
Lasso, Elastic Net, Ridge, L1/L2 logistic, StandardScaler, RobustScaler,
train-only winsorization и train-only rank transform являются диагностическими
экспериментами и не меняют основной production scoring.

## Интерпретация

Небольшое улучшение не считается подтверждённым, если оно нестабильно по fold или
парный bootstrap-интервал включает ноль. Статусы аудита не являются торговой
рекомендацией и не переносятся автоматически в основной скоринг.
