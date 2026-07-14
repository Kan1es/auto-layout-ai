from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.dependencies import utils as fastapi_dependency_utils
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.api import create_api_router
from backend.app.config import DatasetLimits
from backend.app.json_read_write import write_json
from backend.app.models import Dataset, ImageItem
from backend.app.workspace_datasets import DatasetWorkspace


DATASET_LIMITS = DatasetLimits(
    max_zip_mb=10,
    max_extracted_mb=10,
    max_images=150,
    supported_extensions=(".jpg",),
)


class CvatExportApiTest(unittest.TestCase):
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

    def create_dataset(self, root, *, with_annotations=True):
        workspace = DatasetWorkspace(root, "ds_001")
        workspace.create()

        image_path = workspace.image_dir / "image_000001.jpg"
        Image.new("RGB", (100, 200), "white").save(image_path)

        workspace.save_metadata(
            Dataset(
                id="ds_001",
                name="test-dataset",
                status="READY",
                image_count=1,
                images=[
                    ImageItem(
                        id="image_000001",
                        filename="board.jpg",
                        path="images/image_000001.jpg",
                        width=100,
                        height=200,
                    )
                ],
            )
        )

        if with_annotations:
            write_json(
                workspace.annotations_path,
                {
                    "annotations": [
                        {
                            "image_id": "image_000001",
                            "objects": [
                                {
                                    "label": "board",
                                    "confidence": 0.91,
                                    "bbox": {
                                        "x": 10,
                                        "y": 20,
                                        "width": 30,
                                        "height": 40,
                                    },
                                    "mask": None,
                                }
                            ],
                        }
                    ]
                },
            )

        return workspace

    def test_exports_yolo_files_and_archive(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = self.create_dataset(root)
            client = self.make_client(root)

            response = client.post(
                "/api/datasets/ds_001/cvat/export",
                json={"format": "yolo"},
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "OK")
            self.assertEqual(data["format"], "yolo")
            self.assertTrue(data["archive_url"].endswith("/cvat_export/yolo_export.zip"))
            self.assertTrue((workspace.dataset_dir / "cvat_export" / "yolo_export.zip").exists())

            label_path = (
                workspace.dataset_dir
                / "cvat_export"
                / "yolo"
                / "obj_train_data"
                / "board.txt"
            )
            self.assertEqual(
                label_path.read_text(encoding="utf-8"),
                "0 0.250000 0.200000 0.300000 0.200000",
            )

            names_path = workspace.dataset_dir / "cvat_export" / "yolo" / "obj.names"
            self.assertEqual(names_path.read_text(encoding="utf-8"), "board")

    def test_requires_annotations_internal_json(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_dataset(root, with_annotations=False)
            client = self.make_client(root)

            response = client.post(
                "/api/datasets/ds_001/cvat/export",
                json={"format": "yolo"},
            )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "ANNOTATIONS_NOT_FOUND")

    def test_coco_returns_not_supported(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.create_dataset(root)
            client = self.make_client(root)

            response = client.post(
                "/api/datasets/ds_001/cvat/export",
                json={"format": "coco"},
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                response.json()["detail"]["code"],
                "CVAT_EXPORT_FORMAT_NOT_SUPPORTED",
            )


if __name__ == "__main__":
    unittest.main()