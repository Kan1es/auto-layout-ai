# Auto Layout AI

Local MVP prototype for image autolabeling with DART and CVAT import.

## MVP flow

`ZIP with images -> statistics -> representative images -> DART settings -> autolabel -> CVAT export/import`

The current scaffold covers the first two kanban tasks:

- MVP decisions are fixed in `DECISIONS.md`.
- Backend, frontend, config, workspace, and run instructions are in place.
- The selected DART repository is `https://github.com/chen-xin-94/DART`.

## Project structure

```text
backend/              FastAPI application
  app/
    main.py           App entrypoint and healthcheck
    config.py         JSON config loader
frontend/             Static web UI served by backend
config/               Default local configuration
docs/                 Project notes and future documentation
scripts/              Helper scripts for local development
workspace/            Local datasets and generated outputs, ignored by git
models/               Local model weights, ignored by git
```

## Requirements

- Python 3.11+
- Packages from `requirements.txt`

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Optional: copy `config/app.default.json` to `config/app.local.json` and set local DART/CVAT paths.

```powershell
Copy-Item config/app.default.json config/app.local.json
$env:APP_CONFIG_PATH = "config/app.local.json"
```

## Run backend and frontend

```powershell
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

- Frontend: http://127.0.0.1:8000/
- Healthcheck: http://127.0.0.1:8000/health

Expected healthcheck response:

```json
{
  "status": "ok",
  "service": "auto-layout-ai",
  "version": "0.1.0"
}
```

## Notes

- `workspace/` is for local datasets and generated results and is ignored by git.
- `models/` is for local DART weights and is ignored by git.
- Preview images are only for visual review. CVAT export must use original images and real annotations.


--------------------------------------------------------
DART(проверка работы на локальной машине) - входные данные:
- путь к входному изображению;
- подсказка;
- уверенность;
- режим;
- Путь к конфигурации GroundingDINO;
- путь к контрольной точке GroundingDINO.

Пример:
Example:

```powershell
python scripts/dart_single_image.py `
  --image-path "samples/test.jpg" `
  --prompt "bolt" `
  --confidence 0.35 `
  --mode bbox `
  --config-path "D:/DART/GroundingDINO/groundingdino/config/GroundingDINO_SwinT_OGC.py" `
  --checkpoint-path "D:/models/dart/groundingdino/groundingdino_swint_ogc.pth" `
  --output-dir "workspace/dart_single_image/test"
```

1)Create local DART:

py -3.10 -m venv .venv-dart
.\.venv-dart\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel

2)Download
New-Item -ItemType Directory -Force external
cd external

git clone https://github.com/chen-xin-94/DART.git
git clone https://github.com/IDEA-Research/GroundingDINO.git

3)cd D:\auto-layout-ai\external\GroundingDINO

python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
python -m pip install "transformers==4.30.2"
python -m pip install --no-build-isolation -e .

4)New-Item -ItemType Directory -Force D:\auto-layout-ai\models\dart\groundingdino

cd D:\auto-layout-ai\models\dart\groundingdino

Invoke-WebRequest `
  -Uri "https://github.com/IDEA-Research/GroundingDINO/releases/download/v0.1.0-alpha/groundingdino_swint_ogc.pth" `
  -OutFile "groundingdino_swint_ogc.pth"

Пример проверки(в samples надо закинуть jpeg фото):
cd D:\auto-layout-ai\external\GroundingDINO

python demo\inference_on_a_image.py `
  -c groundingdino\config\GroundingDINO_SwinT_OGC.py `
  -p D:\auto-layout-ai\models\dart\groundingdino\groundingdino_swint_ogc.pth `
  -i D:\auto-layout-ai\samples\test.jpg `
  -o D:\auto-layout-ai\workspace\dart_check\official_demo `
  -t "wood board . plank ." `
  --cpu-only