from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import create_api_router
from backend.app.config import DatasetLimits
from scripts.create_smoke_dataset import build_demo_zip


DATASET_LIMITS = DatasetLimits(
    max_zip_mb=10,
    max_extracted_mb=20,
    max_images=10,
    supported_extensions=(".jpg",),
)


class SmokeDartRunner:
    """Deterministic DART substitute for the backend integration smoke test."""

    supported_modes = {"bbox"}

    def __init__(self, *, output_root):
        self.output_root = Path(output_root)

    def run_image(self, image_path, prompt, confidence, mode):
        image_path = Path(image_path)
        return SimpleNamespace(
            raw_result={
                "image_path": str(image_path),
                "prompt": prompt,
                "confidence": confidence,
                "mode": mode,
            },
            normalized_result={
                "image": {
                    "id": image_path.stem,
                    "filename": image_path.name,
                    "width": 256,
                    "height": 160,
                },
                "objects": [
                    {
                        "label": prompt,
                        "confidence": 0.91,
                        "bbox": {"x": 35, "y": 25, "width": 175, "height": 110},
                        "mask": None,
                    }
                ],
            },
        )


class EndToEndSmokeTest(unittest.TestCase):
    def test_backend_flow_from_zip_to_cvat_export(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace_root = root / "workspace"
            demo_zip = build_demo_zip(root / "kan-174-demo.zip")

            app = FastAPI()
            app.include_router(create_api_router(workspace_root, DATASET_LIMITS))
            client = TestClient(app)

            with demo_zip.open("rb") as source:
                upload = client.post(
                    "/api/datasets/upload",
                    files={"file": (demo_zip.name, source, "application/zip")},
                )
            self.assertEqual(upload.status_code, 201, upload.text)
            dataset_id = upload.json()["dataset"]["id"]

            stats = client.get(f"/api/datasets/{dataset_id}/stats")
            self.assertEqual(stats.status_code, 200, stats.text)
            self.assertEqual(stats.json()["stats"]["image_count"], 3)
            self.assertEqual(stats.json()["stats"]["unreadable_image_count"], 0)

            initialized = client.post(
                f"/api/datasets/{dataset_id}/representative/init",
                json={"target_count": 2},
            )
            self.assertEqual(initialized.status_code, 200, initialized.text)

            approved_ids = [initialized.json()["current_image"]["id"]]
            approved = client.post(
                f"/api/datasets/{dataset_id}/representative/approve"
            )
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertFalse(approved.json()["completed"])

            current = client.post(
                f"/api/datasets/{dataset_id}/representative/next"
            )
            self.assertEqual(current.status_code, 200, current.text)
            approved_ids.append(current.json()["current_image"]["id"])
            approved = client.post(
                f"/api/datasets/{dataset_id}/representative/approve"
            )
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertTrue(approved.json()["completed"])
            self.assertEqual(
                set(approved.json()["approved_image_ids"]),
                set(approved_ids),
            )

            settings = {
                "prompt": "box",
                "confidence": 0.35,
                "mode": "bbox",
                "show_overlay": True,
            }
            saved_settings = client.post(
                f"/api/datasets/{dataset_id}/dart/settings",
                json=settings,
            )
            self.assertEqual(saved_settings.status_code, 200, saved_settings.text)

            with patch("backend.app.api.DartRunner", SmokeDartRunner):
                preview = client.post(
                    f"/api/datasets/{dataset_id}/dart/preview",
                    json={"image_id": approved_ids[0], **settings},
                )
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual(preview.json()["objects_count"], 1)

                autolabel = client.post(
                    f"/api/datasets/{dataset_id}/autolabel/start"
                )
                self.assertEqual(autolabel.status_code, 200, autolabel.text)
                self.assertEqual(autolabel.json()["annotated_images"], 3)
                self.assertEqual(autolabel.json()["failed_images"], 0)

            export = client.post(
                f"/api/datasets/{dataset_id}/cvat/export",
                json={"format": "yolo"},
            )
            self.assertEqual(export.status_code, 200, export.text)
            self.assertEqual(export.json()["status"], "OK")

            archive_path = (
                workspace_root
                / "datasets"
                / dataset_id
                / "cvat_export"
                / "yolo_export.zip"
            )
            self.assertTrue(archive_path.exists())
            with ZipFile(archive_path) as archive:
                names = set(archive.namelist())
            self.assertIn("yolo/obj.names", names)
            self.assertEqual(
                len([name for name in names if name.endswith(".txt")]),
                4,
            )

            results = client.get(f"/api/datasets/{dataset_id}/results")
            self.assertEqual(results.status_code, 200, results.text)
            self.assertEqual(len(results.json()["annotations"]), 3)
            self.assertEqual(results.json()["errors"], [])
            self.assertEqual(len(results.json()["previews"]), 3)
            self.assertEqual(results.json()["cvat_export"]["status"], "ready")
            self.assertTrue(
                results.json()["cvat_export"]["archive_url"].endswith(
                    "/cvat_export/yolo_export.zip"
                )
            )


if __name__ == "__main__":
    unittest.main()
