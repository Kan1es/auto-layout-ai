import logging
import random
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import APIRouter, File, HTTPException, UploadFile
from .cvat_export import export_cvat_yolo

from .dataset_zip_importer import import_dataset_zip
from .dart_runner import (
    DartRunner,
    DartRunnerTimeout,
    DartRunnerUnsupportedMode,
)
from .errors import DatasetImportError
from .image_stats import calculate_dataset_stats
from .json_read_write import read_json, write_json
from .preview_renderer import PreviewRenderer
from .models import (
    Annotation,
    DartPreviewRequest,
    DartSettings,
    DartSettingsRequest,
    DatasetError,
    RepresentativeImageResponse,
    RepresentativeInitRequest,
    RepresentativeState,
    RepresentativeStateResponse,
    CvatExportRequest
)
from .workspace_datasets import DatasetWorkspace


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def create_api_router(workspace_root, dataset_limits):
    router = APIRouter(prefix="/api")
    autolabel_start_locks = defaultdict(Lock)

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

    def get_selectable_images(metadata):
        return [
            image
            for image in metadata.get("images", [])
            if image.get("readable", True)
        ]


    def build_representative_response(dataset_id, metadata, state):
        images = get_selectable_images(metadata)
        images_by_id = {
            image["id"]: image
            for image in images
        }

        current_image = None
        if 0 <= state.current_index < len(state.history):
            image = images_by_id.get(state.history[state.current_index])
            if image:
                current_image = RepresentativeImageResponse(
                    id=image["id"],
                    filename=image["filename"],
                    url=to_workspace_url(dataset_dir(dataset_id) / image["path"]),
                    width=image["width"],
                    height=image["height"],
                    approved=image["id"] in state.approved_image_ids,
                )

        request_count = min(
            state.target_count,
            len(images)
        )
        completed = len(state.approved_image_ids) >= request_count
        viewed_image_ids = set(state.history)

        has_unviewed_images = any(
            image["id"] not in viewed_image_ids
            for image in images
        )

        has_forward_history = (
            state.current_index < len(state.history) - 1
        )

        can_go_next = (
            not completed
            and (has_forward_history or has_unviewed_images)
        )

        return RepresentativeStateResponse(
            dataset_id=dataset_id,
            target_count=state.target_count,
            approved_count=len(state.approved_image_ids),
            approved_image_ids=state.approved_image_ids,
            viewed_count=len(state.history),
            total_count=len(images),
            current_image=current_image,
            can_go_prev=state.current_index > 0,
            can_go_next=can_go_next,
            completed=completed,
        )

    @router.get("/datasets/{dataset_id}/results")
    def get_results(dataset_id):
        load_metadata(dataset_id)
        root = dataset_dir(dataset_id)
        results_dir = root / "results"
        annotations_path = results_dir / "annotations_internal.json"
        previews_dir = results_dir / "previews"
        yolo_archive_path = root / "cvat_export" / "yolo_export.zip"
        yolo_folder_path = root / "cvat_export" / "yolo"
        annotations = (
            read_json(annotations_path).get("annotations", [])
            if annotations_path.exists()
            else []
        )
        errors = load_dataset_errors(dataset_id)
        previews = (
            [
                to_workspace_url(path)
                for path in sorted(previews_dir.iterdir())
                if path.is_file()
            ]
            if previews_dir.exists()
            else []
        )
        cvat_export = (
            {
                "status": "ready",
                "format": "yolo",
                "archive_url": to_workspace_url(yolo_archive_path),
                "folder_url": to_workspace_url(yolo_folder_path),
            }
            if yolo_archive_path.exists()
            else {"status": "not_created"}
        )
        return {
            "dataset_id": dataset_id,
            "annotations": annotations,
            "errors": errors,
            "annotations_url": to_workspace_url(results_dir / "annotations_internal.json"),
            "errors_url": to_workspace_url(results_dir / "errors.json"),
            "previews": previews,
            "previews_url": to_workspace_url(previews_dir),
            "cvat_export": cvat_export,
        }

    @router.post("/datasets/{dataset_id}/cvat/export")
    def export_cvat(dataset_id: str, request: CvatExportRequest | None = None):
        metadata = load_metadata(dataset_id)
        workspace = DatasetWorkspace(workspace_root, dataset_id)
        request = request or CvatExportRequest()

        if request.format == "coco":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "CVAT_EXPORT_FORMAT_NOT_SUPPORTED",
                    "message": "COCO export is not available yet. Use YOLO format.",
                    "details": {
                        "dataset_id": dataset_id,
                        "format": request.format,
                        "supported_formats": ["yolo"],
                    },
                },
            )

        if not workspace.annotations_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ANNOTATIONS_NOT_FOUND",
                    "message": "annotations_internal.json was not found.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        annotations_data = read_json(workspace.annotations_path)
        if not annotations_data.get("annotations"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ANNOTATIONS_EMPTY",
                    "message": "annotations_internal.json does not contain annotations.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        return export_cvat_yolo(
            dataset_dir=workspace.dataset_dir,
            workspace_root=workspace_root,
            metadata=metadata,
            annotations_data=annotations_data,
        )

    def now_iso():
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def require_supported_dart_mode(mode):
        if mode not in DartRunner.supported_modes:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "DART_MODE_UNSUPPORTED",
                    "message": f"Режим DART '{mode}' не поддерживается.",
                    "details": {
                        "mode": mode,
                        "supported_modes": sorted(DartRunner.supported_modes),
                    },
                },
            )

    def make_dart_settings(request):
        return DartSettings(
            prompt=request.prompt,
            confidence=request.confidence,
            mode=request.mode,
            show_overlay=request.show_overlay,
        )

    @router.get("/datasets/{dataset_id}/dart/settings")
    def get_dart_settings(dataset_id: str):
        load_metadata(dataset_id)
        workspace = DatasetWorkspace(workspace_root, dataset_id)

        if not workspace.dart_settings_path.exists():
            return {
                "prompt": "",
                "confidence": 0.35,
                "mode": "bbox",
                "show_overlay": True,
                "updated_at": None,
            }

        try:
            return workspace.load_dart_settings().model_dump(mode="json")
        except Exception as error:
            logger.warning(
                "Dataset %s has invalid DART settings: %s", dataset_id, error
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DART_SETTINGS_INVALID",
                    "message": "Сохранённые настройки DART некорректны.",
                    "details": {"dataset_id": dataset_id},
                },
            ) from error

    @router.post("/datasets/{dataset_id}/dart/settings")
    def save_dart_settings(dataset_id: str, request: DartSettingsRequest):
        load_metadata(dataset_id)
        require_supported_dart_mode(request.mode)

        settings = make_dart_settings(request)
        workspace = DatasetWorkspace(workspace_root, dataset_id)
        workspace.save_dart_settings(settings)
        return settings.model_dump(mode="json")

    @router.post("/datasets/{dataset_id}/dart/preview")
    def run_dart_preview(dataset_id: str, request: DartPreviewRequest):
        metadata = load_metadata(dataset_id)
        workspace = DatasetWorkspace(workspace_root, dataset_id)

        if not workspace.representative_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_NOT_INITIALIZED",
                    "message": "Отбор репрезентативных изображений не инициализирован.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        image = next(
            (
                item
                for item in metadata.get("images", [])
                if item.get("id") == request.image_id
            ),
            None,
        )
        if image is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "IMAGE_NOT_FOUND",
                    "message": "Изображение не найдено в датасете.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                    },
                },
            )
        if not image.get("readable", True):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IMAGE_NOT_READABLE",
                    "message": "Изображение невозможно прочитать и использовать для preview.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                    },
                },
            )

        try:
            representative = workspace.load_representative_state()
        except Exception as error:
            logger.warning(
                "Dataset %s has invalid representative state: %s", dataset_id, error
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_STATE_INVALID",
                    "message": "Сохранённое состояние отбора репрезентативных изображений некорректно.",
                    "details": {"dataset_id": dataset_id},
                },
            ) from error

        if request.image_id not in representative.approved_image_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IMAGE_NOT_APPROVED",
                    "message": "Preview можно запускать только для подтверждённых репрезентативных изображений.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                    },
                },
            )

        require_supported_dart_mode(request.mode)
        image_path = dataset_dir(dataset_id) / image.get("path", "")
        runner = DartRunner(output_root=workspace.results_dir / "dart_runs")

        try:
            run_result = runner.run_image(
                image_path=image_path,
                prompt=request.prompt,
                confidence=request.confidence,
                mode=request.mode,
            )
        except DartRunnerUnsupportedMode as error:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "DART_MODE_UNSUPPORTED",
                    "message": f"Режим DART '{request.mode}' не поддерживается.",
                    "details": {"mode": request.mode, "reason": str(error)},
                },
            ) from error
        except DartRunnerTimeout as error:
            logger.warning(
                "DART preview timed out for dataset %s image %s: %s",
                dataset_id,
                request.image_id,
                error,
            )
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "DART_PREVIEW_TIMEOUT",
                    "message": "Время ожидания preview-запуска DART истекло.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                        "reason": str(error),
                    },
                },
            ) from error
        except Exception as error:
            logger.exception(
                "DART preview failed for dataset %s image %s",
                dataset_id,
                request.image_id,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "DART_PREVIEW_FAILED",
                    "message": "Не удалось выполнить preview-запуск DART.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                        "reason": str(error),
                    },
                },
            ) from error

        normalized_result = run_result.normalized_result
        if not isinstance(normalized_result, dict) or not isinstance(
            normalized_result.get("objects", []), list
        ):
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "DART_PREVIEW_INVALID_RESULT",
                    "message": "DART вернул некорректный нормализованный результат.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                    },
                },
            )

        preview_path = workspace.previews_dir / f"{request.image_id}_preview.jpg"
        preview_url = to_workspace_url(preview_path)
        try:
            rendered = PreviewRenderer().render(
                image_path=image_path,
                annotation=normalized_result,
                output_path=preview_path,
                preview_url=preview_url,
            )
        except Exception as error:
            logger.exception(
                "Preview rendering failed for dataset %s image %s",
                dataset_id,
                request.image_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "PREVIEW_RENDER_FAILED",
                    "message": "DART вернул результат, но создать preview-изображение не удалось.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                        "reason": str(error),
                    },
                },
            ) from error

        settings = make_dart_settings(request)
        try:
            workspace.save_preview_results(
                request.image_id,
                run_result.raw_result,
                normalized_result,
            )
            workspace.save_dart_settings(settings)
        except Exception as error:
            logger.exception(
                "Preview artifacts could not be saved for dataset %s image %s",
                dataset_id,
                request.image_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "DART_PREVIEW_SAVE_FAILED",
                    "message": "Не удалось сохранить результаты preview-запуска DART.",
                    "details": {
                        "dataset_id": dataset_id,
                        "image_id": request.image_id,
                    },
                },
            ) from error

        objects_count = rendered["objects_count"]
        return {
            "status": "EMPTY" if objects_count == 0 else "OK",
            "objects_count": objects_count,
            "preview_url": rendered["preview_url"],
            "result": normalized_result,
        }

    def build_autolabel_status(
        *,
        status,
        total_images,
        processed_images=0,
        failed_images=0,
        current_image_id=None,
        started_at=None,
        finished_at=None,
        stop_requested=False,
    ):
        return {
            "status": status,
            "total_images": total_images,
            "processed_images": processed_images,
            "failed_images": failed_images,
            "current_image_id": current_image_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "stop_requested": stop_requested,
        }

    @router.get("/datasets/{dataset_id}/autolabel/status")
    def get_autolabel_status(dataset_id: str):
        load_metadata(dataset_id)
        workspace = DatasetWorkspace(workspace_root, dataset_id)
        return workspace.load_autolabel_status()

    @router.post("/datasets/{dataset_id}/autolabel/stop")
    def stop_autolabel(dataset_id: str):
        load_metadata(dataset_id)
        workspace = DatasetWorkspace(workspace_root, dataset_id)
        status = workspace.load_autolabel_status()

        if status.get("status") not in ["running", "stopping"]:
            return {
                "status": "not_running",
                "dataset_id": dataset_id,
                "message": "Autolabeling is not running.",
                "autolabel": status,
            }

        status["status"] = "stopping"
        status["stop_requested"] = True
        workspace.save_autolabel_status(status)

        return {
            "status": "stopping",
            "dataset_id": dataset_id,
            "message": "Stop request has been saved. Autolabeling will stop between images.",
            "autolabel": status,
        }

    @router.post("/datasets/{dataset_id}/autolabel/start")
    def start_autolabel(dataset_id: str):
        """Run DART over the complete dataset, continuing after image failures.

        This endpoint is intentionally synchronous for the MVP.  Background
        execution, progress reporting, and cancellation are added by TASK-018.
        """
        metadata = load_metadata(dataset_id)
        workspace = DatasetWorkspace(workspace_root, dataset_id)

        if not workspace.dart_settings_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DART_SETTINGS_NOT_FOUND",
                    "message": (
                        "DART settings have not been saved. Configure and save "
                        "DART settings before starting autolabeling."
                    ),
                    "details": {"dataset_id": dataset_id},
                },
            )

        try:
            settings = workspace.load_dart_settings()
        except Exception as error:
            logger.warning(
                "Dataset %s has invalid DART settings: %s", dataset_id, error
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DART_SETTINGS_INVALID",
                    "message": "Saved DART settings are invalid.",
                    "details": {"dataset_id": dataset_id},
                },
            ) from error

        images = metadata.get("images", [])
        runner = DartRunner(output_root=workspace.results_dir / "dart_runs")
        preview_renderer = PreviewRenderer()
        annotations = []
        autolabel_errors = []

        with autolabel_start_locks[dataset_id]:
            current_status = workspace.load_autolabel_status()
            if current_status.get("status") in {"running", "stopping"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "AUTOLABEL_ALREADY_RUNNING",
                        "message": "Авторазметка этого датасета уже выполняется.",
                        "details": {
                            "dataset_id": dataset_id,
                            "status": current_status.get("status"),
                        },
                    },
                )

            started_at = now_iso()
            workspace.save_autolabel_status(
                build_autolabel_status(
                    status="running",
                    total_images=len(images),
                    started_at=started_at,
                )
            )
        try:
            for image in images:
                current_status = workspace.load_autolabel_status()
                if current_status.get("stop_requested"):
                    workspace.save_autolabel_status(
                        build_autolabel_status(
                            status="stopped",
                            total_images=len(images),
                            processed_images=len(annotations) + len(autolabel_errors),
                            failed_images=len(autolabel_errors),
                            current_image_id=None,
                            started_at=started_at,
                            finished_at=now_iso(),
                            stop_requested=True,
                        )
                    )
                    break

                image_id = image.get("id")
                filename = image.get("filename")
                image_path = dataset_dir(dataset_id) / image.get("path", "")

                workspace.save_autolabel_status(
                    build_autolabel_status(
                        status="running",
                        total_images=len(images),
                        processed_images=len(annotations) + len(autolabel_errors),
                        failed_images=len(autolabel_errors),
                        current_image_id=image_id,
                        started_at=started_at,
                    )
                )

                try:
                    result = runner.run_image(
                        image_path=image_path,
                        prompt=settings.prompt,
                        confidence=settings.confidence,
                        mode=settings.mode,
                    )
                    workspace.save_raw_result(image_id, result.raw_result)

                    annotation = Annotation.model_validate(
                        {
                            "image_id": image_id,
                            "objects": result.normalized_result.get("objects", []),
                        }
                    )
                    if settings.show_overlay:
                        preview_path = workspace.previews_dir / f"{image_id}_preview.jpg"
                        preview_renderer.render(
                            image_path=image_path,
                            annotation=annotation,
                            output_path=preview_path,
                            preview_url=to_workspace_url(preview_path),
                        )

                    annotations.append(annotation)

                except Exception as error:
                    logger.warning(
                        "Autolabeling failed for dataset %s image %s: %s",
                        dataset_id,
                        image_id,
                        error,
                    )
                    autolabel_errors.append(
                        DatasetError(
                            stage="autolabel",
                            image_id=image_id,
                            filename=filename,
                            message=str(error),
                            details={"exception_type": type(error).__name__},
                        )
                    )

                workspace.save_autolabel_status(
                    build_autolabel_status(
                        status="running",
                        total_images=len(images),
                        processed_images=len(annotations) + len(autolabel_errors),
                        failed_images=len(autolabel_errors),
                        current_image_id=image_id,
                        started_at=started_at,
                    )
                )

            workspace.save_annotations(annotations)
            all_errors = replace_stage_errors(
                dataset_id,
                "autolabel",
                [error.model_dump(mode="json") for error in autolabel_errors],
            )

        except Exception:
            workspace.save_autolabel_status(
                build_autolabel_status(
                    status="failed",
                    total_images=len(images),
                    processed_images=len(annotations) + len(autolabel_errors),
                    failed_images=len(autolabel_errors),
                    current_image_id=None,
                    started_at=started_at,
                    finished_at=now_iso(),
                    stop_requested=False,
                )
            )
            raise

        final_status = workspace.load_autolabel_status()
        if final_status.get("status") != "stopped":
            final_status = build_autolabel_status(
                status="completed",
                total_images=len(images),
                processed_images=len(annotations) + len(autolabel_errors),
                failed_images=len(autolabel_errors),
                current_image_id=None,
                started_at=started_at,
                finished_at=now_iso(),
                stop_requested=False,
            )
            workspace.save_autolabel_status(final_status)

        return {
            "status": final_status["status"],
            "dataset_id": dataset_id,
            "total_images": len(images),
            "annotated_images": len(annotations),
            "failed_images": len(autolabel_errors),
            "annotations_url": to_workspace_url(workspace.annotations_path),
            "errors_url": to_workspace_url(workspace.errors_path),
            "previews_url": to_workspace_url(workspace.previews_dir),
            "errors": all_errors,
        }

    @router.post(
        "/datasets/{dataset_id}/representative/init",
        response_model=RepresentativeStateResponse
    )
    def init_representative(
        dataset_id: str,
        request: RepresentativeInitRequest,
    ):
        metadata = load_metadata(dataset_id)

        all_images = metadata.get("images", [])
        if not all_images:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DATASET_HAS_NO_IMAGES",
                    "message": "Dataset has no images for representative selection.",
                    "details": {"dataset_id": dataset_id},
                }
            )

        images = get_selectable_images(metadata)

        if not images:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DATASET_HAS_NO_READABLE_IMAGES",
                    "message": "Dataset has no readable images.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        image_ids = [
            image["id"]
            for image in images
        ]
        if len(image_ids) <= request.target_count:
            state = RepresentativeState(
                target_count=request.target_count,
                history=image_ids,
                current_index=0,
                approved_image_ids=image_ids,
            )
        else:
            first_image_id = random.choice(image_ids)
            state = RepresentativeState(
                target_count=request.target_count,
                history=[first_image_id],
                current_index=0,
            )
        workspace = DatasetWorkspace(
            workspace_root,
            dataset_id
        )
        workspace.save_representative_state(state)

        return build_representative_response(
            dataset_id,
            metadata,
            state
        )

    @router.get(
        "/datasets/{dataset_id}/representative/current",
        response_model=RepresentativeStateResponse
    )
    def get_current_representative(dataset_id: str):
        metadata = load_metadata(dataset_id)

        workspace = DatasetWorkspace(
            workspace_root,
            dataset_id
        )

        if not workspace.representative_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_NOT_INITIALIZED",
                    "message": "Representative selection is not initialized.",
                    "details": {"dataset_id": dataset_id},
                }
            )
        state = workspace.load_representative_state()

        return build_representative_response(
            dataset_id,
            metadata,
            state,
        )

    @router.post(
        "/datasets/{dataset_id}/representative/next",
        response_model=RepresentativeStateResponse
    )
    def next_representative(dataset_id: str):
        metadata = load_metadata(dataset_id)

        workspace = DatasetWorkspace(
            workspace_root,
            dataset_id
        )

        if not workspace.representative_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_NOT_INITIALIZED",
                    "message": "Representative selection is not initialized.",
                    "details": {"dataset_id": dataset_id},
                }
            )

        state = workspace.load_representative_state()
        images = get_selectable_images(metadata)
        all_image_ids = [
            image["id"]
            for image in images
        ]
        required_count = min(
            state.target_count,
            len(all_image_ids)
        )
        completed = len(state.approved_image_ids) >= required_count
        if completed:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_SELECTION_COMPLETED",
                    "message": "Representative selection is already completed.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        unviewed_image_ids = [
            image_id
            for image_id in all_image_ids
            if image_id not in state.history
        ]
        has_forward_history = (
            state.current_index < len(state.history) - 1
        )
        if has_forward_history:
            state.current_index += 1
        elif unviewed_image_ids:
            next_image_id = random.choice(unviewed_image_ids)
            state.history.append(next_image_id)
            state.current_index = len(state.history) - 1
        else:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_UNVIEWED_IMAGES",
                    "message": "There are no unviewed images.",
                    "details": {"dataset_id": dataset_id},
                },
            )
        workspace.save_representative_state(state)
        return build_representative_response(
            dataset_id,
            metadata,
            state
        )

    @router.post(
        "/datasets/{dataset_id}/representative/prev",
        response_model=RepresentativeStateResponse
    )
    def prev_representative(dataset_id: str):
        metadata = load_metadata(dataset_id)

        workspace = DatasetWorkspace(
            workspace_root,
            dataset_id
        )

        if not workspace.representative_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_NOT_INITIALIZED",
                    "message": "Representative selection is not initialized.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        state = workspace.load_representative_state()

        if state.current_index <= 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_PREVIOUS_IMAGE",
                    "message": "There is no previous image.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        state.current_index -= 1

        workspace.save_representative_state(state)

        return build_representative_response(
            dataset_id,
            metadata,
            state,
        )

    @router.post(
        "/datasets/{dataset_id}/representative/approve",
        response_model=RepresentativeStateResponse
    )
    def approve_representative(dataset_id: str):
        metadata = load_metadata(dataset_id)

        workspace = DatasetWorkspace(
            workspace_root,
            dataset_id
        )

        if not workspace.representative_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_NOT_INITIALIZED",
                    "message": "Representative selection is not initialized.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        state = workspace.load_representative_state()

        has_current_image = (
            0 <= state.current_index < len(state.history)
        )

        if not has_current_image:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_CURRENT_IMAGE",
                    "message": "There is no current image to approve.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        current_image_id = state.history[state.current_index]

        if current_image_id in state.approved_image_ids:
            return build_representative_response(
                dataset_id,
                metadata,
                state,
            )

        images = get_selectable_images(metadata)

        required_count = min(
            state.target_count,
            len(images),
        )

        if len(state.approved_image_ids) >= required_count:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_SELECTION_COMPLETED",
                    "message": "Representative selection is already completed.",
                    "details": {"dataset_id": dataset_id},
                },
            )
        state.approved_image_ids.append(current_image_id)
        workspace.save_representative_state(state)

        return build_representative_response(
            dataset_id,
            metadata,
            state,
        )

    @router.post(
        "/datasets/{dataset_id}/representative/unapprove",
        response_model=RepresentativeStateResponse
    )
    def unapprove_representative(dataset_id: str):
        metadata = load_metadata(dataset_id)

        workspace = DatasetWorkspace(
            workspace_root,
            dataset_id
        )

        if not workspace.representative_path.exists():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPRESENTATIVE_NOT_INITIALIZED",
                    "message": "Representative selection is not initialized.",
                    "details": {"dataset_id": dataset_id},
                },
            )

        state = workspace.load_representative_state()
        has_current_image = (
            0 <= state.current_index < len(state.history)
        )
        if not has_current_image:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_CURRENT_IMAGE",
                    "message": "There is no current image to unapprove.",
                    "details": {"dataset_id": dataset_id},
                },
            )
        current_image_id = state.history[state.current_index]

        if current_image_id in state.approved_image_ids:
            state.approved_image_ids.remove(current_image_id)
            workspace.save_representative_state(state)

        return build_representative_response(
            dataset_id,
            metadata,
            state,
        )
    return router
