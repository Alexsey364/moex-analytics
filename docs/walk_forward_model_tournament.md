# Walk-forward Model Tournament

Этап 23 является исключительно исследовательским. Он не импортирует и не изменяет
production Decision Engine, не разрешает публикацию вероятностей и не выполняет
автоматическое продвижение моделей.

Для SBERP, LKOH, MTSS, TRNFP и MOEX проверяются горизонты 5, 20, 60 и 120 торговых
сессий. Нейтральные наблюдения определяются политикой, зафиксированной до holdout,
и исключаются только из бинарного direction-duel; непрерывные forward return, MAE,
MFE и touch-targets сохраняют экономический смысл исходного набора.

## Temporal protocol

- последние 15% наблюдений образуют untouched holdout;
- между development и holdout действует embargo не меньше horizon;
- внутри development используются expanding train, validation и pseudo-OOS test;
- imputation и scaling находятся внутри sklearn pipeline и fit только на train;
- все модели и baseline сравниваются на одних датах common sample;
- holdout не участвует в выборе модели, признаков, calibration или ensemble weights.

## Baselines and models

Лучший baseline выбирается внутри development среди unconditional, historical
conditional, momentum и mean reversion. С ним сравниваются linear regularized,
Random Forest, Extra Trees, HistGradientBoosting, kNN diagnostic, regime-specific,
pooled cross-sectional и simple ensemble. Ranking reference использует только
исторически доступный relative momentum и не выдаётся за production signal.

## Gate

Предварительный результат должен иметь положительное OOS improvement, bootstrap
CI, выигрыш не в одном fold, устойчивость по режимам, пройти label-permutation и
random-noise diagnostics. Затем применяется Benjamini–Hochberg FDR. Уже выбранная
модель один раз проверяется на untouched holdout. Провал любого критичного пункта
возвращает champion к baseline. Возможные статусы: `rejected`, `unstable`,
`experimental`, `shadow_candidate`. Статус production отсутствует намеренно.

Результаты сохраняются в immutable research-таблицах `tournament_*`. Незавершённый
run помечается `interrupted` при следующем запуске и не считается завершённым.
