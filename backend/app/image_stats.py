from __future__ import annotations

from collections import Counter
from pathlib import Path
import struct


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class ImageSizeError(ValueError):
    pass


def _read_png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(24)

    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ImageSizeError("invalid PNG header")

    return struct.unpack(">II", header[16:24])


def _read_bmp_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(26)

    if len(header) < 26 or header[:2] != b"BM":
        raise ImageSizeError("invalid BMP header")

    width = struct.unpack("<i", header[18:22])[0]
    height = abs(struct.unpack("<i", header[22:26])[0])
    if width <= 0 or height <= 0:
        raise ImageSizeError("invalid BMP dimensions")

    return width, height


def _read_jpeg_size(path: Path) -> tuple[int, int]:
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3,
        0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB,
        0xCD, 0xCE, 0xCF,
    }

    with path.open("rb") as file:
        if file.read(2) != b"\xff\xd8":
            raise ImageSizeError("invalid JPEG header")

        while True:
            marker_start = file.read(1)
            if not marker_start:
                break
            if marker_start != b"\xff":
                continue

            marker = file.read(1)
            while marker == b"\xff":
                marker = file.read(1)

            if not marker:
                break

            marker_value = marker[0]
            if marker_value in {0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9}:
                continue

            raw_length = file.read(2)
            if len(raw_length) != 2:
                break

            segment_length = struct.unpack(">H", raw_length)[0]
            if segment_length < 2:
                raise ImageSizeError("invalid JPEG segment length")

            if marker_value in sof_markers:
                data = file.read(5)
                if len(data) != 5:
                    break
                height, width = struct.unpack(">HH", data[1:5])
                if width <= 0 or height <= 0:
                    raise ImageSizeError("invalid JPEG dimensions")
                return width, height

            file.seek(segment_length - 2, 1)

    raise ImageSizeError("JPEG dimensions were not found")


def _read_webp_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(30)

    if len(header) < 16 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
        raise ImageSizeError("invalid WebP header")

    chunk = header[12:16]
    if chunk == b"VP8 ":
        if len(header) < 30 or header[23:26] != b"\x9d\x01\x2a":
            raise ImageSizeError("invalid VP8 WebP header")
        width = struct.unpack("<H", header[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", header[28:30])[0] & 0x3FFF
        return width, height

    if chunk == b"VP8L":
        if len(header) < 25 or header[20] != 0x2F:
            raise ImageSizeError("invalid VP8L WebP header")
        bits = int.from_bytes(header[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height

    if chunk == b"VP8X":
        if len(header) < 30:
            raise ImageSizeError("invalid VP8X WebP header")
        width = int.from_bytes(header[24:27], "little") + 1
        height = int.from_bytes(header[27:30], "little") + 1
        return width, height

    raise ImageSizeError("unsupported WebP encoding")


def read_image_size(path: Path) -> tuple[int, int]:
    extension = path.suffix.lower()
    if extension in {".jpg", ".jpeg"}:
        return _read_jpeg_size(path)
    if extension == ".png":
        return _read_png_size(path)
    if extension == ".bmp":
        return _read_bmp_size(path)
    if extension == ".webp":
        return _read_webp_size(path)

    raise ImageSizeError(f"unsupported image extension: {extension}")


def calculate_dataset_stats(dataset_id: str, dataset_root: Path) -> dict:
    images_dir = dataset_root / "images"
    warnings = []
    images = []
    extensions = Counter()
    resolutions = Counter()

    if not images_dir.exists():
        warnings.append("Images directory does not exist.")
    else:
        for path in sorted(images_dir.rglob("*")):
            if not path.is_file():
                continue

            extension = path.suffix.lower()
            if extension not in SUPPORTED_IMAGE_EXTENSIONS:
                continue

            extensions[extension] += 1
            relative_path = path.relative_to(dataset_root).as_posix()
            image_id = path.stem

            try:
                width, height = read_image_size(path)
            except (OSError, ImageSizeError, struct.error) as error:
                message = str(error)
                warnings.append(f"{relative_path}: {message}")
                images.append({
                    "id": image_id,
                    "filename": path.name,
                    "path": relative_path,
                    "width": None,
                    "height": None,
                    "readable": False,
                    "error": message,
                })
                continue

            resolution = f"{width}x{height}"
            resolutions[resolution] += 1
            images.append({
                "id": image_id,
                "filename": path.name,
                "path": relative_path,
                "width": width,
                "height": height,
                "readable": True,
            })

    readable_images = [image for image in images if image["readable"]]
    min_size = None
    max_size = None

    if readable_images:
        min_image = min(readable_images, key=lambda item: item["width"] * item["height"])
        max_image = max(readable_images, key=lambda item: item["width"] * item["height"])
        min_size = {
            "width": min_image["width"],
            "height": min_image["height"],
            "pixels": min_image["width"] * min_image["height"],
        }
        max_size = {
            "width": max_image["width"],
            "height": max_image["height"],
            "pixels": max_image["width"] * max_image["height"],
        }

    return {
        "dataset_id": dataset_id,
        "image_count": len(images),
        "readable_image_count": len(readable_images),
        "unreadable_image_count": len(images) - len(readable_images),
        "extensions": dict(sorted(extensions.items())),
        "min_size": min_size,
        "max_size": max_size,
        "common_resolutions": [
            {"resolution": resolution, "count": count}
            for resolution, count in resolutions.most_common(10)
        ],
        "warnings_count": len(warnings),
        "warnings": warnings,
        "images": images,
    }
