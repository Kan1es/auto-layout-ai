from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.dependencies import utils as fastapi_dependency_utils
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.api import create_api_router
from backend.app.config import DatasetLimits
from backend.app.dart_runner import DartRunnerError, DartRunnerTimeout
from backend.app.models import (
    DartSettings,
    Dataset,
    ImageItem,
    RepresentativeState,
)
from backend.app.workspace_datasets import DatasetWorkspace


DATASET_LIMITS = DatasetLimits(
    max_zip_mb=10,
    max_extracted_mb=10,
    max_images=10,
    supported_extensions=(".jpg",),
)


class SuccessfulDartRunner:
    supported_modes = {"bbox"}
    objects = [
        {
            "label": "bolt",
            "confidence": 0.8,
            "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
            "mask": None,
        }
    ]

    def __init__(self, *, output_root):
        self.output_root = Path(output_root)

    def run_image(self, image_path, prompt, confidence, mode):
        return SimpleNamespace(
            raw_result={"prompt": prompt, "confidence": confidence, "mode": mode},
            normalized_result={
                "image": {"id": Path(image_path).stem},
                "objects": self.objects,
            },
        )


class EmptyDartRunner(SuccessfulDartRunner):
    objects = []


class TimeoutDartRunner(SuccessfulDartRunner):
    def run_image(self, image_path, prompt, confidence, mode):
        raise DartRunnerTimeout("synthetic timeout")


class FailedDartRunner(SuccessfulDartRunner):
    def run_image(self, image_path, prompt, confidence, mode):
        raise DartRunnerError("synthetic DART failure")


class FailedPreviewRenderer:
    def render(self, **kwargs):
        raise RuntimeError("synthetic renderer failure")


class DartPreviewApiTest(unittest.TestCase):
    def make_client(self, root):
        app = FastAPI()
        with patch.object(
            fastapi_dependency_utils,
            "ensure_multipart_is_installed",
        ):
            router = create_api_router(root, DATASET_LIMITS)
        router.routes = [
            route for route in router.routes
            if not route.path.endswith("/datasets/upload")
        ]
        app.include_router(router)
        return TestClient(app)

    def create_dataset(
        self,
        root,
        *,
        representative=True,
        approved=True,
        readable=True,
    ):
        workspace = DatasetWorkspace(root, "ds_001")
        workspace.create()
        image_path = workspace.image_dir / "image_000001.jpg"
        Image.new("RGB", (20, 20), "white").save(image_path)
        workspace.save_metadata(
            Dataset(
                id="ds_001",
                name="test-dataset",
                status="READY",
                image_count=1,
                images=[
                    ImageItem(
                        id="image_000001",
                        filename="source.jpg",
                        path="images/image_000001.jpg",
                        width=20,
                        height=20,
                        readable=readable,
                    )
                ],
            )
        )
        if representative:
            workspace.save_representative_state(
                RepresentativeState(
                    target_count=1,
                    history=["image_000001"],
                    current_index=0,
                    approved_image_ids=["image_000001"] if approved else [],
                )
            )
        return workspace

    def preview_payload(self, **changes):
        payload = {
            "image_id": "image_000001",
            "prompt": "bolt",
            "confidence": 0.35,
            "mode": "bbox",
            "show_overlay": True,
        }
        payload.update(changes)
        return payload

    def test_get_settings_returns_defaults_without_creating_file(self):
        with TemporaryDirectory() as temp_dir:
            workspace = self.create_dataset(Path(temp_dir))
            response = self.make_client(Path(temp_dir)).get(
                "/api/datasets/ds_001/dart/settings"
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {
                    "prompt": "",
                    "confidence": 0.35,
                    "mode": "bbox",
                    "show_overlay": True,
                    "updated_at": None,
                },
            )
            self.assertFalse(workspace.dart_settings_path.exists())

    def test_post_settings_persists_and_is_returned_by_new_client(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = self.create_dataset(root)
            client = self.make_client(root)
            payload = self.preview_payload()
            payload.pop("image_id")

            response = client.post(
                "/api/datasets/ds_001/dart/settings",
                json=payload,
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["prompt"], "bolt")
            self.assertIsNotNone(response.json()["updated_at"])
            self.assertTrue(workspace.dart_settings_path.exists())

            restored = self.make_client(root).get(
                "/api/datasets/ds_001/dart/settings"
            )
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json(), response.json())

    def test_settings_validation_and_unsupported_mode(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_dataset(root)
            client = self.make_client(root)

            empty_prompt = client.post(
                "/api/datasets/ds_001/dart/settings",
                json={
                    "prompt": "   ",
                    "confidence": 0.35,
                    "mode": "bbox",
                    "show_overlay": True,
                },
            )
            invalid_confidence = client.post(
                "/api/datasets/ds_001/dart/settings",
                json={
                    "prompt": "bolt",
                    "confidence": 1.1,
                    "mode": "bbox",
                    "show_overlay": True,
                },
            )
            unsupported = client.post(
                "/api/datasets/ds_001/dart/settings",
                json={
                    "prompt": "bolt",
                    "confidence": 0.35,
                    "mode": "mask",
                    "show_overlay": True,
                },
            )

            self.assertEqual(empty_prompt.status_code, 422)
            self.assertEqual(invalid_confidence.status_code, 422)
            self.assertEqual(unsupported.status_code, 400)
            self.assertEqual(
                unsupported.json()["detail"]["code"], "DART_MODE_UNSUPPORTED"
            )

    def test_preview_saves_artifacts_and_latest_working_settings(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = self.create_dataset(root)
            client = self.make_client(root)

            with patch("backend.app.api.DartRunner", SuccessfulDartRunner):
                response = client.post(
                    "/api/datasets/ds_001/dart/preview",
                    json=self.preview_payload(prompt="  bolt  ", confidence=0.42),
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "OK")
            self.assertEqual(response.json()["objects_count"], 1)
            self.assertEqual(len(response.json()["result"]["objects"]), 1)
            self.assertEqual(
                response.json()["preview_url"],
                "/workspace/datasets/ds_001/results/previews/image_000001_preview.jpg",
            )
            self.assertTrue(
                (workspace.raw_dir / "image_000001_preview_raw.json").exists()
            )
            self.assertTrue(
                (workspace.raw_dir / "image_000001_preview_normalized.json").exists()
            )
            self.assertTrue(
                (workspace.previews_dir / "image_000001_preview.jpg").exists()
            )
            settings = workspace.load_dart_settings()
            self.assertEqual(settings.prompt, "bolt")
            self.assertEqual(settings.confidence, 0.42)

    def test_empty_preview_is_success_and_saves_settings(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = self.create_dataset(root)

            with patch("backend.app.api.DartRunner", EmptyDartRunner):
                response = self.make_client(root).post(
                    "/api/datasets/ds_001/dart/preview",
                    json=self.preview_payload(prompt="nothing"),
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "EMPTY")
            self.assertEqual(response.json()["objects_count"], 0)
            self.assertEqual(workspace.load_dart_settings().prompt, "nothing")

    def test_preview_requires_initialized_approved_readable_image(self):
        scenarios = [
            ({"representative": False}, self.preview_payload(), 409, "REPRESENTATIVE_NOT_INITIALIZED"),
            ({"approved": False}, self.preview_payload(), 409, "IMAGE_NOT_APPROVED"),
            ({"readable": False}, self.preview_payload(), 409, "IMAGE_NOT_READABLE"),
            ({}, self.preview_payload(image_id="missing"), 404, "IMAGE_NOT_FOUND"),
        ]
        for dataset_options, payload, status_code, error_code in scenarios:
            with self.subTest(error_code=error_code):
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    self.create_dataset(root, **dataset_options)
                    response = self.make_client(root).post(
                        "/api/datasets/ds_001/dart/preview",
                        json=payload,
                    )
                    self.assertEqual(response.status_code, status_code)
                    self.assertEqual(response.json()["detail"]["code"], error_code)

    def test_dart_failures_do_not_replace_working_settings_or_artifacts(self):
        runners = [
            (TimeoutDartRunner, 504, "DART_PREVIEW_TIMEOUT"),
            (FailedDartRunner, 502, "DART_PREVIEW_FAILED"),
        ]
        for runner, status_code, error_code in runners:
            with self.subTest(error_code=error_code):
                with TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    workspace = self.create_dataset(root)
                    workspace.save_dart_settings(
                        DartSettings(
                            prompt="working",
                            confidence=0.2,
                            mode="bbox",
                            show_overlay=True,
                        )
                    )

                    with patch("backend.app.api.DartRunner", runner):
                        response = self.make_client(root).post(
                            "/api/datasets/ds_001/dart/preview",
                            json=self.preview_payload(prompt="broken"),
                        )

                    self.assertEqual(response.status_code, status_code)
                    self.assertEqual(response.json()["detail"]["code"], error_code)
                    self.assertEqual(workspace.load_dart_settings().prompt, "working")
                    self.assertFalse(
                        (workspace.raw_dir / "image_000001_preview_raw.json").exists()
                    )
                    self.assertFalse(
                        (workspace.previews_dir / "image_000001_preview.jpg").exists()
                    )

    def test_unsupported_mode_and_renderer_failure_preserve_settings(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = self.create_dataset(root)
            workspace.save_dart_settings(
                DartSettings(
                    prompt="working",
                    confidence=0.2,
                    mode="bbox",
                    show_overlay=True,
                )
            )
            client = self.make_client(root)

            unsupported = client.post(
                "/api/datasets/ds_001/dart/preview",
                json=self.preview_payload(prompt="mask attempt", mode="mask"),
            )
            with patch("backend.app.api.DartRunner", SuccessfulDartRunner):
                with patch("backend.app.api.PreviewRenderer", FailedPreviewRenderer):
                    render_failed = client.post(
                        "/api/datasets/ds_001/dart/preview",
                        json=self.preview_payload(prompt="render attempt"),
                    )

            self.assertEqual(unsupported.status_code, 400)
            self.assertEqual(
                unsupported.json()["detail"]["code"], "DART_MODE_UNSUPPORTED"
            )
            self.assertEqual(render_failed.status_code, 500)
            self.assertEqual(
                render_failed.json()["detail"]["code"], "PREVIEW_RENDER_FAILED"
            )
            self.assertEqual(workspace.load_dart_settings().prompt, "working")
            self.assertFalse(
                (workspace.raw_dir / "image_000001_preview_raw.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
