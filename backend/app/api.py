from pathlib import Path
from datetime import datetime
import logging
from uuid import uuid4
from fastapi import APIRouter, HTTPException, UploadFile, File

from .image_stats import calculate_dataset_stats
from .json_read_write import read_json, write_json
from .models import DatasetError

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def create_api_router(workspace_root):
    router = APIRouter(prefix="/api")

    def dataset_dir(dataset_id):
        return workspace_root / "datasets" / dataset_id

    def load_metadata(dataset_id):
        path = dataset_dir(dataset_id) / "metadata.json"
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "DATASET_NOT_FOUND",
                    "message": "Dataset was not found.",
                    "details": {"dataset_id": dataset_id}
                }
            )
        return read_json(path)

    def save_metadata(dataset_id, metadata):
        write_json(dataset_dir(dataset_id) / "metadata.json", metadata)

    def errors_path(dataset_id):
        return dataset_dir(dataset_id) / "results" / "errors.json"

    def load_dataset_errors(dataset_id):
        path = errors_path(dataset_id)
        if not path.exists():
            return []

        data = read_json(path)
        return data.get("errors", [])

    def save_dataset_errors(dataset_id, errors):
        write_json(errors_path(dataset_id), {"errors": errors})

    def make_dataset_error(stage, image_id, filename, message, details=None):
        return DatasetError(
            stage=stage,
            image_id=image_id,
            filename=filename,
            message=message,
            details=details,
        ).model_dump(mode="json")

    def replace_stage_errors(dataset_id, stage, new_errors):
        errors = [
            error
            for error in load_dataset_errors(dataset_id)
            if error.get("stage") != stage
        ]
        errors.extend(new_errors)
        save_dataset_errors(dataset_id, errors)
        return errors

    def build_dataset_stats(dataset_id):
        metadata = load_metadata(dataset_id)
        stats = calculate_dataset_stats(dataset_id, dataset_dir(dataset_id))
        dataset_errors = [
            make_dataset_error(
                stage="dataset_stats",
                image_id=image.get("id"),
                filename=image.get("filename"),
                message="Image could not be read.",
                details={
                    "path": image.get("path"),
                    "reason": image.get("error"),
                },
            )
            for image in stats["images"]
            if not image.get("readable")
        ]
        replace_stage_errors(dataset_id, "dataset_stats", dataset_errors)

        metadata["stats"] = {
            "image_count": stats["image_count"],
            "readable_image_count": stats["readable_image_count"],
            "unreadable_image_count": stats["unreadable_image_count"],
            "extensions": stats["extensions"],
            "min_size": stats["min_size"],
            "max_size": stats["max_size"],
            "common_resolutions": stats["common_resolutions"],
            "warnings_count": stats["warnings_count"],
        }
        metadata["images"] = stats["images"]
        metadata["image_count"] = stats["image_count"]
        metadata["warnings"] = stats["warnings"]
        save_metadata(dataset_id, metadata)
        if dataset_errors:
            logger.warning(
                "Dataset %s has %s unreadable image(s) during stats calculation.",
                dataset_id,
                len(dataset_errors),
            )
        return stats

    @router.post("/datasets/upload", status_code=201)
    async def upload_dataset(file: UploadFile = File(...)):
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_UPLOAD_TYPE",
                    "message": "Only .zip archives are supported.",
                    "details": {"filename": file.filename}
                }
            )

        dataset_id = f"ds_{uuid4().hex[:12]}"
        root = dataset_dir(dataset_id)

        upload_dir = root / "upload"
        images_dir = root / "images"
        results_dir = root / "results"
        raw_dir = results_dir / "raw"
        previews_dir = results_dir / "previews"
        cvat_dir = root / "cvat_export"

        for path in [upload_dir, images_dir, raw_dir, previews_dir, cvat_dir]:
            path.mkdir(parents=True, exist_ok=True)

        archive_path = upload_dir / "original.zip"
        archive_path.write_bytes(await file.read())

        metadata = {
            "id": dataset_id,
            "name": Path(file.filename).stem,
            "status": "UPLOADED",
            "image_count": 0,
            "created_at": datetime.utcnow().isoformat(),
            "warnings": [
                "ZIP extraction is not implemented in this API MVP yet."
            ],
            "images": [],
            "stats": {
                "image_count": 0,
                "readable_image_count": 0,
                "unreadable_image_count": 0,
                "extensions": {},
                "min_size": None,
                "max_size": None,
                "common_resolutions": [],
                "warnings_count": 1
            }
        }

        write_json(root / "metadata.json", metadata)
        write_json(results_dir / "annotations_internal.json", {"annotations": []})
        write_json(results_dir / "errors.json", {"errors": []})

        return {
            "status": "OK",
            "dataset": metadata,
            "links": {
                "dataset": f"/api/datasets/{dataset_id}",
                "images": f"/api/datasets/{dataset_id}/images",
                "stats": f"/api/datasets/{dataset_id}/stats",
                "results": f"/api/datasets/{dataset_id}/results"
            }
        }

    @router.get("/datasets/{dataset_id}")
    def get_dataset(dataset_id):
        return load_metadata(dataset_id)

    @router.get("/datasets/{dataset_id}/images")
    def get_images(dataset_id):
        metadata = load_metadata(dataset_id)
        return {
            "dataset_id": dataset_id,
            "count": len(metadata.get("images", [])),
            "images": metadata.get("images", [])
        }

    @router.get("/datasets/{dataset_id}/stats")
    def get_stats(dataset_id):
        stats = build_dataset_stats(dataset_id)
        return {
            "dataset_id": dataset_id,
            "stats": {
                "image_count": stats["image_count"],
                "readable_image_count": stats["readable_image_count"],
                "unreadable_image_count": stats["unreadable_image_count"],
                "extensions": stats["extensions"],
                "min_size": stats["min_size"],
                "max_size": stats["max_size"],
                "common_resolutions": stats["common_resolutions"],
                "warnings_count": stats["warnings_count"],
            },
            "warnings": stats["warnings"],
            "images": stats["images"],
        }

    def to_workspace_url(path):
        relative_path = path.relative_to(workspace_root).as_posix()
        return f"/workspace/{relative_path}"

    @router.get("/datasets/{dataset_id}/results")
    def get_results(dataset_id):
        load_metadata(dataset_id)
        root = dataset_dir(dataset_id)
        results_dir = root / "results"
        annotations_path = results_dir / "annotations_internal.json"
        annotations = (
            read_json(annotations_path).get("annotations", [])
            if annotations_path.exists()
            else []
        )
        errors = load_dataset_errors(dataset_id)
        return {
            "dataset_id": dataset_id,
            "annotations": annotations,
            "errors": errors,
            "annotations_url": to_workspace_url(results_dir / "annotations_internal.json"),
            "errors_url": to_workspace_url(results_dir / "errors.json"),
            "previews_url": to_workspace_url(results_dir / "previews"),
            "dart": {"status": "stub"},
            "cvat": {"status": "stub"},
        }
    return router
