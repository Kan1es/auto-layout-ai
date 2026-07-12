from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from .config import PROJECT_ROOT

DartMode = Literal["bbox", "mask", "bbox_and_mask"]


class DartRunnerError(RuntimeError):
    pass

class DartRunnerTimeout(DartRunnerError):
    pass

class DartRunnerUnsupportedMode(DartRunnerError):
    pass

@dataclass(frozen=True)
class DartRunResult:
    image_path: Path
    output_dir: Path
    raw_result_path: Path
    normalized_result_path: Path
    preview_path: Path | None
    raw_result: dict[str, Any]
    normalized_result: dict[str, Any]

class DartRunner:
    supported_modes = {"bbox"}

    def __init__(
        self,
        *,
        script_path: Path | str | None = None,
        config_path: Path | str | None = None,
        checkpoint_path: Path | str | None = None,
        output_root: Path | str | None = None,
        python_executable: Path | str | None = None,
        timeout_seconds: int = 300,
        device: Literal["cpu", "cuda"] = "cpu",
        text_threshold: float = 0.25,
    ):
        self.script_path = Path(script_path or PROJECT_ROOT / "scripts" / "dart_test.py")
        self.config_path = Path(
            config_path
            or PROJECT_ROOT
            / "external"
            / "GroundingDINO"
            / "groundingdino"
            / "config"
            / "GroundingDINO_SwinT_OGC.py"
        )
        self.checkpoint_path = Path(
            checkpoint_path
            or PROJECT_ROOT
            / "models"
            / "dart"
            / "groundingdino"
            / "groundingdino_swint_ogc.pth"
        )
        self.output_root = Path(output_root or PROJECT_ROOT / "workspace" / "dart_runs")
        self.python_executable = str(
            python_executable
            or os.getenv("DART_PYTHON")
            or sys.executable
        )
        self.timeout_seconds = timeout_seconds
        self.device = device
        self.text_threshold = text_threshold

    def run_image(
        self,
        image_path: Path | str,
        prompt: str,
        confidence: float,
        mode: DartMode = "bbox"
    ):
        image_path = Path(image_path)
        self._validate_inputs(image_path, prompt, confidence, mode)

        output_dir = self.output_root / f"{image_path.stem}_{uuid4().hex[:8]}"
        output_dir.mkdir(parents=True, exist_ok=True)

        command = [
            self.python_executable,
            str(self.script_path),
            "--image-path",
            str(image_path),
            "--prompt",
            prompt,
            "--confidence",
            str(confidence),
            "--mode",
            mode,
            "--config-path",
            str(self.config_path),
            "--checkpoint-path",
            str(self.checkpoint_path),
            "--output-dir",
            str(output_dir),
            "--text-threshold",
            str(self.text_threshold),
            "--device",
            self.device
        ]

        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False
            )
        except subprocess.TimeoutExpired as error:
            raise DartRunnerTimeout(
                f"DART timed out after {self.timeout_seconds} seconds."
            ) from error
        except OSError as error:
            raise DartRunnerError(f"DART process could not be started: {error}") from error

        if completed.returncode != 0:
            raise DartRunnerError(
                "DART process failed. "
                f"Exit code: {completed.returncode}. "
                f"STDOUT: {completed.stdout.strip()} "
                f"STDERR: {completed.stderr.strip()}"
            )

        return self._load_result(image_path, output_dir)

    def _validate_inputs(
        self,
        image_path: Path,
        prompt: str,
        confidence: float,
        mode: DartMode
    ):
        if mode not in self.supported_modes:
            raise DartRunnerUnsupportedMode(
                f"DART mode '{mode}' is not supported by this runner yet. "
                f"Available modes: {', '.join(sorted(self.supported_modes))}."
            )
        if not image_path.exists():
            raise DartRunnerError(f"Image not found: {image_path}")
        if not prompt.strip():
            raise DartRunnerError("Prompt must not be empty.")
        if confidence < 0 or confidence > 1:
            raise DartRunnerError("Confidence must be between 0 and 1.")
        if not self.script_path.exists():
            raise DartRunnerError(f"DART script not found: {self.script_path}")
        if not self.config_path.exists():
            raise DartRunnerError(f"GroundingDINO config not found: {self.config_path}")
        if not self.checkpoint_path.exists():
            raise DartRunnerError(f"GroundingDINO checkpoint not found: {self.checkpoint_path}")

    def _load_result(self, image_path: Path, output_dir: Path) -> DartRunResult:
        raw_result_path = output_dir / "raw_result.json"
        normalized_result_path = output_dir / "normalized_result.json"
        preview_path = output_dir / "preview.jpg"

        if not raw_result_path.exists():
            raise DartRunnerError(f"DART raw result was not created: {raw_result_path}")
        if not normalized_result_path.exists():
            raise DartRunnerError(
                f"DART normalized result was not created: {normalized_result_path}"
            )

        return DartRunResult(
            image_path=image_path,
            output_dir=output_dir,
            raw_result_path=raw_result_path,
            normalized_result_path=normalized_result_path,
            preview_path=preview_path if preview_path.exists() else None,
            raw_result=self._read_json(raw_result_path),
            normalized_result=self._read_json(normalized_result_path)
        )

    def _read_json(self, path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise DartRunnerError(f"DART result is not valid JSON: {path}") from error