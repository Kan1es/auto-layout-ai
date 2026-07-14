from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any


def _workspace_url(path: Path, workspace_root: Path) -> str:
    return f"/workspace/{path.relative_to(workspace_root).as_posix()}"


def _bbox_to_yolo_line(
    *,
    class_id: int,
    bbox: dict[str, Any],
    image_width: int,
    image_height: int,
) -> str:
    x = float(bbox["x"])
    y = float(bbox["y"])
    width = float(bbox["width"])
    height = float(bbox["height"])

    x_center_norm = (x + width / 2) / image_width
    y_center_norm = (y + height / 2) / image_height
    width_norm = width / image_width
    height_norm = height / image_height

    values = [x_center_norm, y_center_norm, width_norm, height_norm]
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("Normalized bbox values must be in range 0..1.")

    return (
        f"{class_id} "
        f"{x_center_norm:.6f} "
        f"{y_center_norm:.6f} "
        f"{width_norm:.6f} "
        f"{height_norm:.6f}"
    )


def _resolve_image(annotation: dict[str, Any], images_by_id: dict[str, dict[str, Any]]):
    if annotation.get("image"):
        return annotation["image"]

    image_id = annotation.get("image_id")
    return images_by_id.get(image_id)


def export_cvat_yolo(
    *,
    dataset_dir: Path,
    workspace_root: Path,
    metadata: dict[str, Any],
    annotations_data: dict[str, Any],
) -> dict[str, Any]:
    export_root = dataset_dir / "cvat_export" / "yolo"
    data_dir = export_root / "obj_train_data"
    archive_path = dataset_dir / "cvat_export" / "yolo_export.zip"
    warnings: list[str] = []

    if export_root.exists():
        shutil.rmtree(export_root)

    data_dir.mkdir(parents=True, exist_ok=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    annotations = annotations_data.get("annotations", [])
    images_by_id = {
        image["id"]: image
        for image in metadata.get("images", [])
        if image.get("id")
    }

    labels = sorted(
        {
            obj.get("label")
            for annotation in annotations
            for obj in annotation.get("objects", [])
            if obj.get("label") and obj.get("bbox")
        }
    )
    class_ids = {label: index for index, label in enumerate(labels)}

    used_names: set[str] = set()
    train_lines: list[str] = []

    for annotation in annotations:
        image = _resolve_image(annotation, images_by_id)
        if not image:
            warnings.append(f"Image metadata was not found for annotation: {annotation.get('image_id')}.")
            continue

        image_width = image.get("width")
        image_height = image.get("height")
        if not image_width or not image_height:
            warnings.append(f"Image has invalid size: {image.get('id')}.")
            continue

        source_path = dataset_dir / image.get("path", "")
        if not source_path.exists():
            warnings.append(f"Source image was not found: {source_path}.")
            continue

        output_name = Path(image.get("filename") or source_path.name).name
        if output_name in used_names:
            output_name = f"{image.get('id')}_{output_name}"
        used_names.add(output_name)

        output_image_path = data_dir / output_name
        output_label_path = data_dir / f"{Path(output_name).stem}.txt"

        shutil.copy2(source_path, output_image_path)

        yolo_lines: list[str] = []
        for obj in annotation.get("objects", []):
            label = obj.get("label")
            bbox = obj.get("bbox")

            if not label or bbox is None:
                continue

            try:
                yolo_lines.append(
                    _bbox_to_yolo_line(
                        class_id=class_ids[label],
                        bbox=bbox,
                        image_width=int(image_width),
                        image_height=int(image_height),
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                warnings.append(
                    f"Object was skipped for image {image.get('id')}: {error}"
                )

        output_label_path.write_text("\n".join(yolo_lines), encoding="utf-8")
        train_lines.append(f"obj_train_data/{output_name}")

    (export_root / "obj.names").write_text("\n".join(labels), encoding="utf-8")
    (export_root / "train.txt").write_text("\n".join(train_lines), encoding="utf-8")
    (export_root / "obj.data").write_text(
        "\n".join(
            [
                f"classes = {len(labels)}",
                "train = train.txt",
                "names = obj.names",
                "backup = backup/",
            ]
        ),
        encoding="utf-8",
    )

    if archive_path.exists():
        archive_path.unlink()

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in export_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(export_root.parent))

    files = [
        _workspace_url(file_path, workspace_root)
        for file_path in sorted(export_root.rglob("*"))
        if file_path.is_file()
    ]

    return {
        "status": "OK",
        "format": "yolo",
        "files": files,
        "folder_url": _workspace_url(export_root, workspace_root),
        "archive_url": _workspace_url(archive_path, workspace_root),
        "warnings": warnings,
    }