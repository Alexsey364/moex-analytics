# Версионирование модели SBER

Frozen rule содержит rule_version, configuration_hash, activation/retirement и периоды
development, validation, holdout. Production и пять shadow вариантов получают одинаковый cutoff:
production+operational, without technical, without valuation, staged baseline, buy-and-hold.
Operational evidence начинает с experimental_weight_zero.
