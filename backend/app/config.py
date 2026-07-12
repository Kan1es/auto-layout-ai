from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app.default.json"


@dataclass(frozen=True)
class WorkspaceConfig:
    root: Path


@dataclass(frozen=True)
class DartConfig:
    repository: str
    weights_path: Path
    default_mode: str


@dataclass(frozen=True)
class CvatConfig:
    url: str
    manual_import: bool


@dataclass(frozen=True)
class DatasetLimits:
    max_zip_mb: int
    max_extracted_mb: int
    max_images: int
    supported_extensions: tuple[str, ...]


@dataclass(frozen=True)
class AppConfig:
    service_name: str
    version: str
    workspace: WorkspaceConfig
    dart: DartConfig
    cvat: CvatConfig
    dataset_limits: DatasetLimits


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _read_config_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_config() -> AppConfig:
    configured_path = os.getenv("APP_CONFIG_PATH")
    config_path = _resolve_path(configured_path) if configured_path else DEFAULT_CONFIG_PATH
    raw = _read_config_file(config_path)

    return AppConfig(
        service_name=raw.get("service_name", "auto-layout-ai"),
        version=raw.get("version", "0.1.0"),
        workspace=WorkspaceConfig(root=_resolve_path(raw["workspace"]["root"])),
        dart=DartConfig(
            repository=raw["dart"]["repository"],
            weights_path=_resolve_path(raw["dart"]["weights_path"]),
            default_mode=raw["dart"].get("default_mode", "bbox"),
        ),
        cvat=CvatConfig(
            url=raw["cvat"].get("url", "http://localhost:8080"),
            manual_import=bool(raw["cvat"].get("manual_import", True)),
        ),
        dataset_limits=DatasetLimits(
            max_zip_mb=int(raw["dataset_limits"]["max_zip_mb"]),
            max_extracted_mb=int(raw["dataset_limits"]["max_extracted_mb"]),
            max_images=int(raw["dataset_limits"]["max_images"]),
            supported_extensions=tuple(raw["dataset_limits"]["supported_extensions"]),
        ),
    )
