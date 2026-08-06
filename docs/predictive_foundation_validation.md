# Predictive coverage and ablation

Coverage audit хранит диапазон, observations, missingness, lag, revisions, timezone и пригодные
горизонты. Ablation использует одну common sample; блоки сравниваются с baseline по горизонтам
1/5/20/60/120/250. Lead/lag является диагностикой, не доказательством причинности. На этом этапе
ни один блок не становится финальной production direction-моделью.
