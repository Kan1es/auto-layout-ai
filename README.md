«Веб-сервис авторазметки датасетов с DART и загрузкой в CVAT» - Байзульдинов З. С., Кузьмичев А. А., Шипулин Л. М., Коростелев М.С., Журавлев Д. А. (Б-ИФСТ-11)

# Auto Layout AI

Локальный MVP-сервис для автоматической разметки изображений через DART/GroundingDINO и подготовки YOLO-экспорта для CVAT.

Основной сценарий:

```text
ZIP с изображениями
  → статистика датасета
  → выбор репрезентативных кадров
  → настройка DART и preview
  → авторазметка всего датасета
  → YOLO-экспорт
  → ручной импорт в CVAT
```

## Текущий статус

Backend-компоненты сценария реализованы и покрыты тестами. Smoke test KAN-174 проходит полную API-цепочку до YOLO-архива с детерминированным заменителем DART.

Основные frontend/backend контракты стабилизированы в KAN-196. Для полного сценария реальный DART и CVAT по-прежнему должны быть установлены отдельно; импорт YOLO-архива выполняется вручную. Результаты интеграционной проверки зафиксированы в [`docs/kan-174-smoke-report.md`](docs/kan-174-smoke-report.md).

## Структура проекта

```text
backend/app/          FastAPI backend и REST API
frontend/             Статический web-интерфейс
config/               Конфигурация приложения
docs/                 Форматы и отчёты проверок
scripts/              DART и вспомогательные скрипты
tests/                Unit, API и smoke tests
workspace/            Датасеты и результаты, не хранится в Git
external/             Локальные репозитории DART/GroundingDINO, не хранится в Git
models/               Локальные веса моделей, не хранятся в Git
```

## Требования

Для основного приложения:

- Windows и PowerShell;
- Python 3.11+;
- зависимости из `requirements.txt`.

Для реального DART-запуска дополнительно требуются:

- отдельное Python 3.10-окружение рекомендуется для совместимости GroundingDINO;
- PyTorch;
- GroundingDINO и его зависимости;
- checkpoint `groundingdino_swint_ogc.pth`.

Для финального ручного импорта нужен локальный CVAT, обычно доступный по `http://localhost:8080`.

## Установка приложения

Из корня репозитория:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Если Python 3.11 зарегистрирован под другой командой, создайте окружение доступным интерпретатором версии 3.11 или новее.

### Локальная конфигурация

По умолчанию используется `config/app.default.json`. Чтобы переопределить настройки без изменения default-файла:

```powershell
Copy-Item config/app.default.json config/app.local.json
$env:APP_CONFIG_PATH = "config/app.local.json"
```

Основные значения по умолчанию:

- workspace: `workspace/`;
- CVAT: `http://localhost:8080`;
- максимальный ZIP: 512 МБ;
- максимальный распакованный размер: 2048 МБ;
- максимум изображений: 150;
- форматы: JPG, JPEG, PNG, BMP, WEBP.

## Настройка DART/GroundingDINO

Текущий MVP фактически поддерживает только режим `bbox`. Режимы `mask` и `bbox_and_mask` присутствуют в некоторых контрактах, но отклоняются текущим `DartRunner` как неподдерживаемые.

### 1. Создать отдельное окружение

```powershell
py -3.10 -m venv .venv-dart
.\.venv-dart\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
```

### 2. Скачать репозитории

```powershell
New-Item -ItemType Directory -Force external
git clone https://github.com/chen-xin-94/DART.git external/DART
git clone https://github.com/IDEA-Research/GroundingDINO.git external/GroundingDINO
```

### 3. Установить GroundingDINO

CPU-вариант:

```powershell
Set-Location external/GroundingDINO
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
python -m pip install "transformers==4.30.2"
python -m pip install --no-build-isolation -e .
Set-Location ../..
```

Для CUDA установите сборку PyTorch, соответствующую вашей версии CUDA, вместо CPU-команды.

### 4. Скачать checkpoint

```powershell
New-Item -ItemType Directory -Force models/dart/groundingdino
Invoke-WebRequest `
  -Uri "https://github.com/IDEA-Research/GroundingDINO/releases/download/v0.1.0-alpha/groundingdino_swint_ogc.pth" `
  -OutFile "models/dart/groundingdino/groundingdino_swint_ogc.pth"
```

Ожидаемые пути текущего `DartRunner`:

```text
external/GroundingDINO/groundingdino/config/GroundingDINO_SwinT_OGC.py
models/dart/groundingdino/groundingdino_swint_ogc.pth
scripts/dart_test.py
```

Чтобы backend запускал DART через отдельное окружение:

```powershell
$env:DART_PYTHON = (Resolve-Path ".venv-dart/Scripts/python.exe")
```

Переменная должна быть задана в том же терминале до запуска backend.

### 5. Проверить DART на одном изображении

```powershell
.\.venv-dart\Scripts\Activate.ps1
python scripts/dart_test.py `
  --image-path "samples/test.jpg" `
  --prompt "bolt" `
  --confidence 0.35 `
  --mode bbox `
  --config-path "external/GroundingDINO/groundingdino/config/GroundingDINO_SwinT_OGC.py" `
  --checkpoint-path "models/dart/groundingdino/groundingdino_swint_ogc.pth" `
  --output-dir "workspace/dart_single_image/test" `
  --device cpu
```

При успехе создаются:

```text
workspace/dart_single_image/test/raw_result.json
workspace/dart_single_image/test/normalized_result.json
workspace/dart_single_image/test/preview.jpg
```

## Запуск backend и frontend

```powershell
.\.venv\Scripts\Activate.ps1
$env:DART_PYTHON = (Resolve-Path ".venv-dart/Scripts/python.exe")
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Открыть:

- UI: http://127.0.0.1:8000/;
- healthcheck: http://127.0.0.1:8000/health;
- OpenAPI: http://127.0.0.1:8000/docs;
- CVAT: http://localhost:8080/.

Пример healthcheck:

```json
{
  "status": "ok",
  "service": "auto-layout-ai",
  "version": "0.1.0"
}
```

## Demo-датасет и smoke test

Создать маленький ZIP с тремя изображениями:

```powershell
.\.venv\Scripts\python.exe scripts/create_smoke_dataset.py
```

Результат:

```text
workspace/smoke/kan-174-demo.zip
```

Запустить только end-to-end backend smoke test:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_e2e_smoke -v
```

Запустить весь тестовый набор:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Smoke test использует заменитель DART и проверяет интеграцию приложения, но не подтверждает качество или работоспособность реальной модели.

## Демонстрационный сценарий

Перед демонстрацией нужно убедиться, что DART и CVAT доступны локально.

Перед демонстрацией запустить локальный CVAT.

1. Открыть `http://127.0.0.1:8000`.
2. Загрузить `workspace/smoke/kan-174-demo.zip` или другой небольшой ZIP.
3. Показать статистику: количество изображений, расширения, размеры и warnings.
4. Задать число репрезентативных кадров и подтвердить выбранные изображения.
5. На экране DART задать `prompt`, `confidence` и режим `bbox`.
6. Запустить preview и проверить overlay, label, confidence и число объектов.
7. Запустить авторазметку всего датасета и дождаться статуса `completed`.
8. Открыть результаты: internal JSON, preview и список ошибок.
9. Подготовить YOLO-экспорт для CVAT.
10. Импортировать архив в локальный CVAT и показать исходные изображения с настоящими bbox-аннотациями.

Preview используется только для визуальной проверки. Экспорт строится из исходных изображений и `annotations_internal.json`.

## Ручной экспорт и импорт в CVAT

Backend поддерживает YOLO-экспорт:

```text
POST /api/datasets/{dataset_id}/cvat/export
Content-Type: application/json

{"format":"yolo"}
```

Архив создаётся по пути:

```text
workspace/datasets/{dataset_id}/cvat_export/yolo_export.zip
```

В локальном CVAT:

1. Создать задачу и добавить labels из `yolo/obj.names` в том же порядке.
2. Загрузить исходные изображения из `yolo/obj_train_data/`.
3. В меню задачи выбрать загрузку annotations.
4. Выбрать формат YOLO и передать `yolo_export.zip`.
5. Открыть несколько кадров и проверить совпадение bbox с объектами.

Автоматический endpoint `/api/datasets/{dataset_id}/cvat/import` в текущей версии отсутствует. После подготовки экспорта UI открывает локальный CVAT для ручного импорта.

## Результаты обработки

Для каждого датасета используется структура:

```text
workspace/datasets/{dataset_id}/
  metadata.json
  representative.json
  dart_settings.json
  images/
  results/
    annotations_internal.json
    errors.json
    raw/
    previews/
  cvat_export/
```

- `annotations_internal.json` — нормализованная разметка для экспорта;
- `errors.json` — ошибки по отдельным изображениям и этапам;
- `raw/` — исходные результаты DART;
- `previews/` — изображения с визуальным overlay;
- `cvat_export/` — сформированные файлы и архив YOLO.

## Ограничения MVP

- Поддерживается только bbox-разметка.
- Авторазметка выполняется синхронно; большие датасеты могут долго удерживать HTTP-запрос.
- Остановка проверяется между изображениями, а не внутри одного DART-запуска.
- Preview не является источником аннотаций для CVAT.
- Ошибка отдельного изображения сохраняется в `errors.json` и не должна останавливать остальные изображения.
- Workspace и веса не хранятся в Git.
- Автоматический импорт в CVAT не реализован.

## Troubleshooting

### `DART script/config/checkpoint not found`

Проверить наличие:

```text
scripts/dart_test.py
external/GroundingDINO/groundingdino/config/GroundingDINO_SwinT_OGC.py
models/dart/groundingdino/groundingdino_swint_ogc.pth
```

### `GroundingDINO is not importable`

Активировать `.venv-dart` и повторить установку GroundingDINO с `--no-build-isolation -e .`. Убедиться, что `DART_PYTHON` указывает на Python этого окружения.

### Ошибки CUDA или PyTorch

Для проверки переключиться на CPU. Standalone-скрипту передать `--device cpu`; для CUDA установить совместимую сборку PyTorch.

### DART timeout

На CPU первый запуск может быть медленным. По умолчанию `DartRunner` ждёт до 300 секунд. Проверить нагрузку, пути к весам и отдельный запуск `scripts/dart_test.py`.

### DART вернул пустой результат

Это не техническая ошибка. Попробовать уточнить prompt или уменьшить confidence. Preview API вернёт статус `EMPTY` и сохранит технически успешную конфигурацию.

### Режим mask не работает

Это ожидаемое ограничение MVP. Использовать `mode=bbox`.

### ZIP не загружается

Проверить расширение архива, поддерживаемые форматы изображений, отсутствие повреждённых файлов и лимиты из `config/app.default.json`.

### Импорт в CVAT не запускается из UI

Автоматический endpoint не реализован. Кнопка открывает локальный CVAT; использовать ручной YOLO-экспорт и импорт по инструкции выше.
