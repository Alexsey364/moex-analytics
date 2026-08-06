# Point-in-time events

`occurred_at`, `published_at` и `available_from` хранят разные моменты и не подменяют друг друга. Исторический расчёт видит событие только после `available_from`. Повторные публикации объединяются canonical key по сущности, типу, дню и метрике; сохраняются source copies и первое официальное время.
