from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, load_config


config = load_config()
app = FastAPI(title="Auto Layout AI", version=config.version)

frontend_dir = PROJECT_ROOT / "frontend"
static_dir = frontend_dir / "static"

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.get("/health")
def healthcheck() -> dict[str, object]:
    return {
        "status": "ok",
        "service": config.service_name,
        "version": config.version,
        "workspace": str(config.workspace.root),
        "limits": {
            "max_zip_mb": config.dataset_limits.max_zip_mb,
            "max_images": config.dataset_limits.max_images,
        },
    }


@app.on_event("startup")
def ensure_workspace() -> None:
    Path(config.workspace.root).mkdir(parents=True, exist_ok=True)
