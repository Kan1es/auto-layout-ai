# KAN-174 — end-to-end smoke test

Дата проверки: 2026-07-15.

## Подготовленный сценарий

- `scripts/create_smoke_dataset.py` создаёт `workspace/smoke/kan-174-demo.zip` с тремя читаемыми JPEG-изображениями.
- `tests/test_e2e_smoke.py` воспроизводит backend-цепочку: ZIP upload → statistics → representative selection → DART settings → preview → autolabel → YOLO export → results.
- Во время автоматического теста используется детерминированный заменитель DART. Он проверяет интеграцию компонентов приложения, но не заменяет проверку реальной модели.

## Результат прогона

| Этап | Результат | Примечание |
|---|---|---|
| Создание demo ZIP | PASS | Три JPEG 256×160 |
| Backend smoke test | PASS с workaround | Для продолжения после representative/next тест временно инъецирует отсутствующий модуль `random` |
| Запуск backend/frontend | PASS | Backend отвечает на `/health`, UI открывается на `127.0.0.1:8000` |
| Live upload и statistics | PASS | Demo ZIP загружен в запущенный backend, три изображения прочитаны без warnings |
| Пользовательский путь через UI | FAIL, contract check | Frontend не извлекает `dataset.id` из фактического ответа upload |
| Representative next в live backend | FAIL | HTTP 500 из-за отсутствующего импорта `random` |
| Реальный DART preview/autolabel | BLOCKED | Нет `external/GroundingDINO`, весов и Python-окружения DART |
| Импорт в локальный CVAT | BLOCKED | CVAT не запущен; backend endpoint `/api/datasets/{dataset_id}/cvat/import` отсутствует |

## Найденные проблемы

1. Backend endpoint representative next падает с `NameError`: используется `random.choice`, но модуль `random` не импортирован.
2. Upload API возвращает датасет в `response.dataset`, а frontend читает `response.id` или `response.dataset_id`. После успешной загрузки UI теряет идентификатор датасета.
3. Stats API возвращает значения в `response.stats`, а frontend ожидает основные поля статистики в корне ответа.
4. Frontend отправляет `{ "n": ... }` в representative init, backend ожидает `{ "target_count": ... }`.
5. Backend после representative init не выбирает первый текущий кадр; UI сразу запрашивает current и ожидает изображение.
6. Representative API возвращает `current_image` и `target_count`, frontend читает `image` и `target_n`.
7. После CVAT export frontend ищет `path` или `export_path`, backend возвращает `archive_url` и `folder_url`.
8. Frontend вызывает `/cvat/import`, но такой endpoint не зарегистрирован.
9. Results API возвращает URL каталога previews, а frontend ожидает массив URL preview-изображений.

## Статус критериев приёмки

- Полный пользовательский путь без ручного редактирования файлов: **не выполнен** из-за перечисленных контрактных расхождений и отсутствующих внешних сервисов.
- Исходные изображения и настоящая DART-разметка в CVAT: **не проверено**, DART и CVAT локально недоступны.
- Список известных ограничений: **подготовлен выше**.

Исправление найденных контрактных расхождений относится к KAN-196 (интеграционная стабилизация), а реализация CVAT import — к профильной задаче CVAT. В рамках KAN-174 проблемы только зафиксированы.
