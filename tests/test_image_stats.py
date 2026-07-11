from pathlib import Path
import importlib.util
import json
import struct
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import create_api_router
from backend.app.image_stats import calculate_dataset_stats


def write_png(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def write_jpeg(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"\xff\xd8"
        + b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        + b"\xff\xd9"
    )


def write_bmp(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"BM"
        + b"\x00" * 16
        + struct.pack("<i", width)
        + struct.pack("<i", height)
    )


def write_webp(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"RIFF"
        + b"\x1e\x00\x00\x00"
        + b"WEBP"
        + b"VP8X"
        + b"\x0a\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )


class ImageStatsTest(unittest.TestCase):
    def test_calculates_mixed_dataset_stats_and_warnings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_root = Path(temp_dir) / "datasets" / "sample"
            images_dir = dataset_root / "images"
            images_dir.mkdir(parents=True)

            write_jpeg(images_dir / "first.jpg", 640, 480)
            write_png(images_dir / "second.png", 800, 600)
            write_webp(images_dir / "third.webp", 640, 480)
            write_bmp(images_dir / "fourth.bmp", 320, 240)
            (images_dir / "broken.jpg").write_bytes(b"not a jpeg")

            stats = calculate_dataset_stats("sample", dataset_root)

        self.assertEqual(stats["image_count"], 5)
        self.assertEqual(stats["readable_image_count"], 4)
        self.assertEqual(stats["unreadable_image_count"], 1)
        self.assertEqual(stats["extensions"], {
            ".bmp": 1,
            ".jpg": 2,
            ".png": 1,
            ".webp": 1,
        })
        self.assertEqual(stats["min_size"], {
            "width": 320,
            "height": 240,
            "pixels": 76800,
        })
        self.assertEqual(stats["max_size"], {
            "width": 800,
            "height": 600,
            "pixels": 480000,
        })
        self.assertEqual(stats["common_resolutions"][0], {
            "resolution": "640x480",
            "count": 2,
        })
        self.assertEqual(stats["warnings_count"], 1)
        self.assertIn("broken.jpg", stats["warnings"][0])

    @unittest.skipIf(
        importlib.util.find_spec("multipart") is None,
        "python-multipart is required to register upload routes",
    )
    def test_stats_api_returns_calculated_dataset_stats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspace"
            dataset_root = workspace_root / "datasets" / "sample"
            images_dir = dataset_root / "images"
            images_dir.mkdir(parents=True)
            write_png(images_dir / "image.png", 1024, 768)
            (dataset_root / "metadata.json").write_text(
                json.dumps({
                    "id": "sample",
                    "name": "sample",
                    "status": "UPLOADED",
                    "image_count": 0,
                    "warnings": [],
                    "images": [],
                    "stats": {},
                }),
                encoding="utf-8",
            )

            app = FastAPI()
            app.include_router(create_api_router(workspace_root))
            response = TestClient(app).get("/api/datasets/sample/stats")

            metadata = json.loads((dataset_root / "metadata.json").read_text(encoding="utf-8"))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stats"]["image_count"], 1)
        self.assertEqual(body["stats"]["extensions"], {".png": 1})
        self.assertEqual(body["images"][0]["width"], 1024)
        self.assertEqual(metadata["stats"]["image_count"], 1)
        self.assertEqual(metadata["images"][0]["height"], 768)

    @unittest.skipIf(
        importlib.util.find_spec("multipart") is None,
        "python-multipart is required to register upload routes",
    )
    def test_stats_api_saves_unreadable_image_error_in_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir) / "workspace"
            dataset_root = workspace_root / "datasets" / "sample"
            images_dir = dataset_root / "images"
            results_dir = dataset_root / "results"
            images_dir.mkdir(parents=True)
            results_dir.mkdir(parents=True)
            (images_dir / "broken.jpg").write_bytes(b"not a jpeg")
            (dataset_root / "metadata.json").write_text(
                json.dumps({
                    "id": "sample",
                    "name": "sample",
                    "status": "UPLOADED",
                    "image_count": 0,
                    "warnings": [],
                    "images": [],
                    "stats": {},
                }),
                encoding="utf-8",
            )
            (results_dir / "errors.json").write_text(
                json.dumps({"errors": []}),
                encoding="utf-8",
            )

            app = FastAPI()
            app.include_router(create_api_router(workspace_root))
            client = TestClient(app)
            stats_response = client.get("/api/datasets/sample/stats")
            results_response = client.get("/api/datasets/sample/results")

            errors_file = json.loads(
                (results_dir / "errors.json").read_text(encoding="utf-8")
            )

        self.assertEqual(stats_response.status_code, 200)
        self.assertEqual(results_response.status_code, 200)
        self.assertEqual(len(errors_file["errors"]), 1)
        self.assertEqual(errors_file["errors"][0]["stage"], "dataset_stats")
        self.assertEqual(errors_file["errors"][0]["image_id"], "broken")
        self.assertEqual(errors_file["errors"][0]["filename"], "broken.jpg")
        self.assertEqual(
            errors_file["errors"][0]["details"]["reason"],
            "invalid JPEG header",
        )
        self.assertIn("created_at", errors_file["errors"][0])
        self.assertEqual(results_response.json()["errors"], errors_file["errors"])


if __name__ == "__main__":
    unittest.main()
