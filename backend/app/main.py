from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, load_config
from .api import create_api_router

config = load_config()
app = FastAPI(title="Auto Layout AI", version=config.version)

frontend_dir = PROJECT_ROOT / "frontend"
static_dir = frontend_dir / "static"

workspace_root = Path(config.workspace.root)
workspace_root.mkdir(parents=True, exist_ok=True)

# frontend сможет теперь жить отдельно?
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/workspace", StaticFiles(directory=workspace_root), name="workspace")
app.include_router(create_api_router(workspace_root, config.dataset_limits))

def error_response(status_code, code, message, details = None):
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details
            }
        }
    )

@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return error_response(
            exc.status_code,
            exc.detail.get("code", "HTTP_ERROR"),
            exc.detail.get("message", str(exc.detail)),
            exc.detail.get("details", {})
        )

    return error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))

@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    return error_response(
        422,
        "VALIDATION_ERROR",
        "Request validation failed.",
        {"errors": exc.errors()}
    )

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