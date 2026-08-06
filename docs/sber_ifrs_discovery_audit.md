# Аудит discovery МСФО SBER

Дата проверки: 2026-08-06.

Проверенные официальные точки:

- https://www.sberbank.com/investor-relations
- https://www.sberbank.com/ru/investor-relations/reports-and-publications
- https://www.moex.com/ru/listing/emidocs.aspx?id=484
- https://www.e-disclosure.ru/portal/company.aspx?id=3043

Факт базы после проверки: запрос fundamental_documents для accounting_standard IFRS возвращает
0 строк. Это не означает отсутствия IFRS-релизов у Сбера. Текущий crawler не извлекает документы
из динамической структуры официального архива, поэтому нет document_id, локального файла,
страницы и таблицы, которые можно было бы передать parser/review.

Следствие для каждого IFRS-релиза в официальном архиве одинаково: документ ещё не прошёл
discovery и download, поэтому validation невозможна. Ни один показатель не повышен до validated.
Для исправления нужен отдельный адаптер динамического IR-архива либо контролируемый список
официальных URL с file hash, после чего PDF/XLSX должен получить заполненный review-шаблон с
точной страницей и таблицей.

Статус: blocker documented; IFRS features не допускаются в predictive ablation и production.
