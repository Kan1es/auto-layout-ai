from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.dependencies import utils as fastapi_dependency_utils
from fastapi.testclient import TestClient

from backend.app.api import create_api_router
from backend.app.config import DatasetLimits
from backend.app.models import DartSettings, Dataset, ImageItem
from backend.app.workspace_datasets import DatasetWorkspace


DATASET_LIMITS = DatasetLimits(
    max_zip_mb=10,
    max_extracted_mb=10,
    max_images=150,
    supported_extensions=(".jpg",),
)


class FakeDartRunner:
    def __init__(self, *, output_root):
        self.output_root = Path(output_root)

    def run_image(self, image_path, prompt, confidence, mode):
        image_path = Path(image_path)
        if image_path.stem == "image_000002":
            raise RuntimeError("synthetic DART failure")

        preview_path = self.output_root / f"{image_path.stem}.jpg"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"preview")
        return SimpleNamespace(
            raw_result={"image": image_path.name, "prompt": prompt},
            normalized_result={
                "objects": [
                    {
                        "label": prompt,
                        "confidence": confidence,
                        "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                        "mask": None,
                    }
                ]
            },
            preview_path=preview_path,
        )


class AutolabelApiTest(unittest.TestCase):
    def make_client(self, root):
        app = FastAPI()
        # The endpoint under test has no multipart input.  The test environment
        # intentionally does not install the optional upload dependency, while
        # router creation also registers the upload route.
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

    def create_dataset(self, root, *, save_settings=True):
        workspace = DatasetWorkspace(root, "ds_001")
        workspace.create()
        images = []
        for index in range(1, 4):
            image_id = f"image_{index:06d}"
            relative_path = f"images/{image_id}.jpg"
            (workspace.dataset_dir / relative_path).write_bytes(b"image")
            images.append(
                ImageItem(
                    id=image_id,
                    filename=f"source-{index}.jpg",
                    path=relative_path,
                    width=10,
                    height=20,
                )
            )
        workspace.save_metadata(
            Dataset(
                id="ds_001",
                name="test-dataset",
                status="READY",
                image_count=len(images),
                images=images,
            )
        )
        if save_settings:
            workspace.save_dart_settings(
                DartSettings(
                    prompt="bolt",
                    confidence=0.35,
                    mode="bbox",
                    show_overlay=True,
                )
            )
        return workspace

    def test_autolabel_processes_remaining_images_after_failure(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = self.create_dataset(root)
            client = self.make_client(root)

            with patch("backend.app.api.DartRunner", FakeDartRunner):
                response = client.post("/api/datasets/ds_001/autolabel/start")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "completed")
            self.assertEqual(response.json()["annotated_images"], 2)
            self.assertEqual(response.json()["failed_images"], 1)

            annotations = workspace.load_annotations()
            self.assertEqual([item.image_id for item in annotations], ["image_000001", "image_000003"])
            self.assertEqual(workspace.load_raw_result("image_000003")["prompt"], "bolt")
            self.assertTrue((workspace.previews_dir / "image_000001_preview.jpg").exists())

            errors = workspace.load_errors()
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].stage, "autolabel")
            self.assertEqual(errors[0].image_id, "image_000002")

    def test_autolabel_requires_saved_dart_settings(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_dataset(root, save_settings=False)
            client = self.make_client(root)

            response = client.post("/api/datasets/ds_001/autolabel/start")

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "DART_SETTINGS_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
