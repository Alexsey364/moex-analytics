# Live paper tracking SBER

Live snapshot неизменяем: уникальны cutoff и model version, payload имеет SHA-256. Повторный
запуск не создаёт запись. Исправление правил означает новую версию. Outcomes записываются лишь
после полного горизонта 1/5/20/60/120/250 сессий. Historical, pseudo-out-of-sample и live имеют
разные sample_type. Это paper research; заявок брокеру нет.
