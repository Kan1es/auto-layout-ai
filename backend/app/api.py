from datetime import datetime
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from .dataset_zip_importer import import_dataset_zip
from .errors import DatasetImportError
from .image_stats import calculate_dataset_stats
from .json_read_write import read_json, write_json
from .models import DatasetError
from .workspace_datasets import DatasetWorkspace


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def create_api_router(workspace_root, dataset_limits):
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
                    "details": {"filename": file.filename},
                },
            )

        dataset_id = f"ds_{uuid4().hex[:12]}"
        dataset_name = Path(file.filename).stem
        max_size_bytes = dataset_limits.max_zip_mb * 1024 * 1024
        chunk_size = 1024 * 1024
        uploaded_size = 0

        with TemporaryDirectory() as temp_dir:
            temp_zip_path = Path(temp_dir) / "upload.zip"

            with temp_zip_path.open("wb") as target:
                while True:
                    chunk = await file.read(chunk_size)

                    if not chunk:
                        break

                    uploaded_size += len(chunk)

                    if uploaded_size > max_size_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail={
                                "code": "DATASET_ARCHIVE_TOO_LARGE",
                                "message": "ZIP archive is too large.",
                                "details": {
                                    "max_zip_mb": dataset_limits.max_zip_mb,
                                },
                            },
                        )

                    target.write(chunk)
            try:
                dataset = import_dataset_zip(
                    zip_path=temp_zip_path,
                    dataset_id=dataset_id,
                    dataset_name=dataset_name,
                    workspace_root=workspace_root,
                    limits=dataset_limits,
                )
            except DatasetImportError as error:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "DATASET_IMPORT_FAILED",
                        "message": str(error),
                        "details": {
                            "filename": file.filename,
                        },
                    },
                ) from error

        workspace = DatasetWorkspace(
            workspace_root,
            dataset_id,
        )
        workspace.save_annotations([])
        workspace.save_errors([])

        return {
            "status": "OK",
            "dataset": dataset.model_dump(mode="json"),
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
