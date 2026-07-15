import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import create_api_router
from backend.app.config import DatasetLimits


DATASET_LIMITS = DatasetLimits(
    max_zip_mb=10,
    max_extracted_mb=20,
    max_images=100,
    supported_extensions=(
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".webp",
    ),
)


def create_test_client(workspace_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_api_router(workspace_root, DATASET_LIMITS))
    return TestClient(app)


def make_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + data)

    return (
        struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)
    )


def write_png(path: Path, width: int, height: int) -> None:
    signature = b"\x89PNG\r\n\x1a\n"

    ihdr = struct.pack(
        ">IIBBBBB",
        width,
        height,
        8,  # bit depth
        2,  # RGB color
        0,  # compression method
        0,  # filter method
        0,  # no interlace
    )

    raw_pixels = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))

    path.write_bytes(
        signature
        + make_png_chunk(b"IHDR", ihdr)
        + make_png_chunk(b"IDAT", zlib.compress(raw_pixels))
        + make_png_chunk(b"IEND", b"")
    )


class DatasetApiTest(unittest.TestCase):
    def test_uploads_valid_zip_with_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace_root = temp_path / "workspace"
            client = create_test_client(workspace_root)

            image_path = temp_path / "sample.png"
            write_png(image_path, 640, 480)

            zip_path = temp_path / "valid.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(image_path, arcname="sample.png")

            response = client.post(
                "/api/datasets/upload",
                files={
                    "file": (
                        "valid.zip",
                        zip_path.read_bytes(),
                        "application/zip",
                    ),
                },
            )

            self.assertEqual(response.status_code, 201)

            data = response.json()
            dataset = data["dataset"]

            image = dataset["images"][0]

            self.assertEqual(image["id"], "image_000001")
            self.assertEqual(image["filename"], "sample.png")
            self.assertEqual(
                image["path"],
                "images/image_000001.png",
            )

            self.assertEqual(dataset["image_count"], 1)
            self.assertEqual(len(dataset["images"]), 1)

            dataset_id = dataset["id"]
            dataset_root = workspace_root / "datasets" / dataset_id

            stored_image_path = dataset_root / "images" / "image_000001.png"

            self.assertTrue(stored_image_path.exists())
            self.assertEqual(
                stored_image_path.read_bytes(),
                image_path.read_bytes(),
            )
            stored_zip_path = dataset_root / "upload" / "original.zip"

            self.assertTrue(stored_zip_path.exists())
            self.assertEqual(
                stored_zip_path.read_bytes(),
                zip_path.read_bytes(),
            )
            self.assertTrue((dataset_root / "metadata.json").exists())

            stats_response = client.get(f"/api/datasets/{dataset_id}/stats")
            self.assertEqual(stats_response.status_code, 200)

            stats = stats_response.json()["stats"]
            self.assertEqual(stats["image_count"], 1)
            self.assertEqual(stats["readable_image_count"], 1)
            self.assertEqual(stats["unreadable_image_count"], 0)

    def test_rejects_zip_without_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace_root = temp_path / "workspace"
            client = create_test_client(workspace_root)

            zip_path = temp_path / "without_images.zip"

            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("notes.txt", "This archive has no images.")

            response = client.post(
                "/api/datasets/upload",
                files={
                    "file": (
                        "without_images.zip",
                        zip_path.read_bytes(),
                        "application/zip",
                    ),
                },
            )

            self.assertEqual(response.status_code, 400)

            error = response.json()["detail"]
            self.assertEqual(error["code"], "DATASET_IMPORT_FAILED")
            self.assertEqual(
                error["message"],
                "ZIP не содержит поддерживаемых изображений",
            )
            self.assertEqual(
                error["details"]["filename"],
                "without_images.zip",
            )
            self.assertFalse((workspace_root / "datasets").exists())

    def test_rejects_corrupted_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace_root = temp_path / "workspace"
            client = create_test_client(workspace_root)

            zip_path = temp_path / "corrupted.zip"
            zip_path.write_bytes(b"This is not a ZIP archive.")

            response = client.post(
                "/api/datasets/upload",
                files={
                    "file": (
                        "corrupted.zip",
                        zip_path.read_bytes(),
                        "application/zip",
                    ),
                },
            )

            self.assertEqual(response.status_code, 400)

            error = response.json()["detail"]
            self.assertEqual(error["code"], "DATASET_IMPORT_FAILED")
            self.assertEqual(
                error["message"],
                "ZIP-архив поврежден или имеет неверный формат",
            )
            self.assertEqual(
                error["details"]["filename"],
                "corrupted.zip",
            )
            self.assertFalse((workspace_root / "datasets").exists())

    def test_skips_unsupported_files_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace_root = temp_path / "workspace"
            client = create_test_client(workspace_root)

            image_path = temp_path / "sample.png"
            write_png(image_path, 640, 480)

            zip_path = temp_path / "mixed.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(image_path, arcname="sample.png")
                archive.writestr(
                    "notes.txt",
                    "This file is not an image.",
                )

            response = client.post(
                "/api/datasets/upload",
                files={
                    "file": (
                        "mixed.zip",
                        zip_path.read_bytes(),
                        "application/zip",
                    ),
                },
            )

            self.assertEqual(response.status_code, 201)

            dataset = response.json()["dataset"]
            self.assertEqual(dataset["image_count"], 1)
            self.assertEqual(len(dataset["images"]), 1)
            self.assertEqual(
                dataset["warnings"],
                ["Файл пропущен: notes.txt"],
            )

            dataset_root = workspace_root / "datasets" / dataset["id"]
            self.assertTrue((dataset_root / "images" / "image_000001.png").exists())
            self.assertFalse((dataset_root / "images" / "notes.txt").exists())

    def test_excess_target_auto_approves_all_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace_root = temp_path / "workspace"
            client = create_test_client(workspace_root)

            first_image_path = temp_path / "first.png"
            second_image_path = temp_path / "second.png"
            write_png(first_image_path, 640, 480)
            write_png(second_image_path, 800, 600)

            zip_path = temp_path / "two_images.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(first_image_path, arcname="first.png")
                archive.write(second_image_path, arcname="second.png")

            upload_response = client.post(
                "/api/datasets/upload",
                files={
                    "file": (
                        "two_images.zip",
                        zip_path.read_bytes(),
                        "application/zip",
                    ),
                },
            )
            self.assertEqual(upload_response.status_code, 201)

            uploaded_dataset = upload_response.json()["dataset"]
            dataset_id = uploaded_dataset["id"]
            image_ids = [image["id"] for image in uploaded_dataset["images"]]
            init_response = client.post(
                f"/api/datasets/{dataset_id}/representative/init",
                json={"target_count": 5},
            )

            self.assertEqual(init_response.status_code, 200)

            state = init_response.json()
            self.assertEqual(state["dataset_id"], dataset_id)
            self.assertEqual(state["target_count"], 5)
            self.assertEqual(state["approved_count"], 2)
            self.assertEqual(state["approved_image_ids"], image_ids)
            self.assertEqual(state["viewed_count"], 2)
            self.assertEqual(state["total_count"], 2)
            self.assertEqual(state["current_image"]["id"], image_ids[0])
            self.assertTrue(state["current_image"]["approved"])
            self.assertFalse(state["can_go_prev"])
            self.assertFalse(state["can_go_next"])
            self.assertTrue(state["completed"])

            current_response = client.get(
                f"/api/datasets/{dataset_id}/representative/current"
            )
            self.assertEqual(current_response.status_code, 200)
            self.assertEqual(current_response.json(), state)

    def test_repeated_approve_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace_root = temp_path / "workspace"
            client = create_test_client(workspace_root)

            first_image_path = temp_path / "first.png"
            second_image_path = temp_path / "second.png"
            write_png(first_image_path, 640, 480)
            write_png(second_image_path, 800, 600)

            zip_path = temp_path / "two_images.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(first_image_path, arcname="first.png")
                archive.write(second_image_path, arcname="second.png")

            upload_response = client.post(
                "/api/datasets/upload",
                files={
                    "file": (
                        "two_images.zip",
                        zip_path.read_bytes(),
                        "application/zip",
                    ),
                },
            )
            self.assertEqual(upload_response.status_code, 201)
            dataset_id = upload_response.json()["dataset"]["id"]

            init_response = client.post(
                f"/api/datasets/{dataset_id}/representative/init",
                json={"target_count": 1},
            )
            self.assertEqual(init_response.status_code, 200)
            self.assertEqual(init_response.json()["approved_count"], 0)
            self.assertFalse(init_response.json()["completed"])

            next_response = client.post(
                f"/api/datasets/{dataset_id}/representative/next"
            )
            self.assertEqual(next_response.status_code, 200)

            next_state = next_response.json()
            selected_image_id = next_state["current_image"]["id"]
            self.assertEqual(next_state["approved_count"], 0)
            self.assertFalse(next_state["current_image"]["approved"])

            first_approve_response = client.post(
                f"/api/datasets/{dataset_id}/representative/approve"
            )
            self.assertEqual(first_approve_response.status_code, 200)

            first_approved_state = first_approve_response.json()
            self.assertEqual(first_approved_state["approved_count"], 1)
            self.assertEqual(
                first_approved_state["current_image"]["id"],
                selected_image_id,
            )
            self.assertTrue(first_approved_state["current_image"]["approved"])
            self.assertTrue(first_approved_state["completed"])
            self.assertFalse(first_approved_state["can_go_next"])

            second_approve_response = client.post(
                f"/api/datasets/{dataset_id}/representative/approve"
            )
            self.assertEqual(second_approve_response.status_code, 200)
            self.assertEqual(
                second_approve_response.json(),
                first_approved_state,
            )

            current_response = client.get(
                f"/api/datasets/{dataset_id}/representative/current"
            )
            self.assertEqual(current_response.status_code, 200)
            self.assertEqual(current_response.json(), first_approved_state)

    def test_prev_next_preserve_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            workspace_root = temp_path / "workspace"
            client = create_test_client(workspace_root)

            first_image_path = temp_path / "first.png"
            second_image_path = temp_path / "second.png"
            third_image_path = temp_path / "third.png"
            write_png(first_image_path, 640, 480)
            write_png(second_image_path, 800, 600)
            write_png(third_image_path, 1024, 768)

            zip_path = temp_path / "three_images.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(first_image_path, arcname="first.png")
                archive.write(second_image_path, arcname="second.png")
                archive.write(third_image_path, arcname="third.png")

            upload_response = client.post(
                "/api/datasets/upload",
                files={
                    "file": (
                        "three_images.zip",
                        zip_path.read_bytes(),
                        "application/zip",
                    ),
                },
            )
            self.assertEqual(upload_response.status_code, 201)
            dataset_id = upload_response.json()["dataset"]["id"]

            init_response = client.post(
                f"/api/datasets/{dataset_id}/representative/init",
                json={"target_count": 2},
            )
            self.assertEqual(init_response.status_code, 200)

            first_state = init_response.json()
            first_image_id = first_state["current_image"]["id"]
            self.assertEqual(first_state["viewed_count"], 1)
            self.assertFalse(first_state["can_go_prev"])
            self.assertTrue(first_state["can_go_next"])

            next_response = client.post(
                f"/api/datasets/{dataset_id}/representative/next"
            )
            self.assertEqual(next_response.status_code, 200)

            second_state = next_response.json()
            second_image_id = second_state["current_image"]["id"]
            self.assertNotEqual(second_image_id, first_image_id)
            self.assertEqual(second_state["viewed_count"], 2)
            self.assertTrue(second_state["can_go_prev"])

            prev_response = client.post(
                f"/api/datasets/{dataset_id}/representative/prev"
            )
            self.assertEqual(prev_response.status_code, 200)

            prev_state = prev_response.json()
            self.assertEqual(
                prev_state["current_image"]["id"],
                first_image_id,
            )
            self.assertEqual(prev_state["viewed_count"], 2)
            self.assertFalse(prev_state["can_go_prev"])
            self.assertTrue(prev_state["can_go_next"])

            forward_response = client.post(
                f"/api/datasets/{dataset_id}/representative/next"
            )
            self.assertEqual(forward_response.status_code, 200)

            forward_state = forward_response.json()
            self.assertEqual(
                forward_state["current_image"]["id"],
                second_image_id,
            )
            self.assertEqual(forward_state["viewed_count"], 2)
            self.assertEqual(forward_state, second_state)

            current_response = client.get(
                f"/api/datasets/{dataset_id}/representative/current"
            )
            self.assertEqual(current_response.status_code, 200)
            self.assertEqual(current_response.json(), second_state)
