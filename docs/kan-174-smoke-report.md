# KAN-174 — end-to-end smoke test

Дата первоначальной проверки: 2026-07-15. Повторная проверка после KAN-196: 2026-07-15.

## Подготовленный сценарий

- `scripts/create_smoke_dataset.py` создаёт `workspace/smoke/kan-174-demo.zip` с тремя читаемыми JPEG-изображениями.
- `tests/test_e2e_smoke.py` воспроизводит backend-цепочку: ZIP upload → statistics → representative selection → DART settings → preview → autolabel → YOLO export → results.
- Во время автоматического теста используется детерминированный заменитель DART. Он проверяет интеграцию компонентов приложения, но не заменяет проверку реальной модели.

## Результат повторного прогона

| Этап | Результат | Примечание |
|---|---|---|
| Создание demo ZIP | PASS | Три JPEG 256×160 |
| Backend smoke test | PASS | Полная API-цепочка до YOLO-архива без workaround |
| Upload и statistics contract/live API | PASS | Live API прочитал три изображения; frontend использует `response.dataset.id` и вложенный блок `stats` |
| Representative selection | PASS | Init возвращает текущий кадр, next/prev и approved IDs согласованы |
| DART settings и preview contract | PASS | UI использует поддерживаемый режим bbox и сохранённые настройки |
| Autolabel status contract | PASS | UI обрабатывает `completed`, `failed` и `stopped` |
| Results и preview URLs | PASS | API возвращает массив preview и состояние YOLO-экспорта |
| YOLO export | PASS | Архив строится из последнего `annotations_internal.json` |
| Реальный DART preview/autolabel | NOT RUN | Требуются локальные GroundingDINO, checkpoint и отдельное окружение DART |
| Импорт в локальный CVAT | MANUAL | UI открывает CVAT; автоматический import endpoint не реализован |

## Исправленные интеграционные проблемы

1. Добавлен отсутствовавший импорт `random`; representative next больше не падает с `NameError`.
2. Frontend извлекает датасет из `response.dataset` после upload.
3. Frontend читает статистику из `response.stats` и использует реальные поля размеров/resolutions.
4. Representative init отправляет `target_count`, сразу получает текущий кадр и использует `current_image`.
5. Representative API возвращает `approved_image_ids`; UI синхронизирует список кадров для preview.
6. UI обрабатывает фактические статусы и поля прогресса autolabel.
7. UI показывает `archive_url` YOLO-экспорта и предлагает ручной импорт вместо вызова отсутствующего endpoint.
8. Results API возвращает реальные preview URL и состояние CVAT export вместо заглушек.
9. Пользовательские API-ошибки корректно извлекаются из общего формата `error.message`.

## Оставшиеся ограничения

- Реальный DART не проверен в этой среде: отсутствуют `external/GroundingDINO`, веса и `.venv-dart`.
- Локальный CVAT не был запущен; импорт остаётся ручным и описан в README.
- Авторазметка MVP выполняется синхронно и может долго удерживать HTTP-запрос.
- Поддерживается только режим bbox.

## Статус критериев приёмки

- Интеграционная API-цепочка без ручного редактирования результатов: **PASS** с тестовым заменителем DART.
- Настоящая DART-разметка в CVAT: **NOT RUN**, требует внешнего окружения DART/CVAT.
- Список известных ограничений: **актуализирован выше и в README**.
