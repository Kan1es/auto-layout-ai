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
