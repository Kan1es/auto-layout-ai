from pathlib import Path
from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, HTTPException, UploadFile, File

from .json_read_write import read_json, write_json

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
        metadata = load_metadata(dataset_id)
        return {
            "dataset_id": dataset_id,
            "stats": metadata.get("stats", {}),
            "warnings": metadata.get("warnings", [])
        }

    def to_workspace_url(path):
        relative_path = path.relative_to(workspace_root).as_posix()
        return f"/workspace/{relative_path}"

    @router.get("/datasets/{dataset_id}/results")
    def get_results(dataset_id):
        load_metadata(dataset_id)
        root = dataset_dir(dataset_id)
        results_dir = root / "results"
        return {
            "dataset_id": dataset_id,
            "annotations_url": to_workspace_url(results_dir / "annotations_internal.json"),
            "errors_url": to_workspace_url(results_dir / "errors.json"),
            "previews_url": to_workspace_url(results_dir / "previews"),
            "dart": {"status": "stub"},
            "cvat": {"status": "stub"},
        }
    return router