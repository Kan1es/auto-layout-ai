import shutil
import zipfile
from pathlib import Path, PurePosixPath

from .config import DatasetLimits
from .errors import DatasetImportError
from .models import Dataset, ImageItem
from .workspace_datasets import DatasetWorkspace


def is_safe_zip_member(name: str) -> bool:
    if "\\" in name:
        return False

    path = PurePosixPath(name)

    if path.is_absolute():
        return False

    if '..' in path.parts:
        return False

    return True


def import_dataset_zip(
    zip_path: Path,
    dataset_id: str,
    dataset_name: str,
    workspace_root: Path,
    limits: DatasetLimits,
):
    max_size_bytes = limits.max_zip_mb * 1024 * 1024

    max_extracted_size_bytes = limits.max_extracted_mb * 1024 * 1024


    if zip_path.suffix.lower() != ".zip":
        raise DatasetImportError("Поддерживаются только .zip архивы")

    if zip_path.stat().st_size > max_size_bytes:
        raise DatasetImportError("ZIP-архив слишком большой")

    try:
        with zipfile.ZipFile(zip_path) as archive:
            image_members = []
            warnings = []

            for member in archive.infolist():
                if member.is_dir():
                    continue

                if not is_safe_zip_member(member.filename):
                    raise DatasetImportError(
                        f"ZIP содержит небезопасный путь: {member.filename}"
                    )

                member_path = PurePosixPath(member.filename)

                if (
                    "__MACOSX" in member_path.parts
                    or member_path.name.startswith("._")
                    or member_path.name == ".DS_Store"
                ):
                    warnings.append(f"Служебный файл пропущен: {member.filename}")
                    continue

                extension = member_path.suffix.lower()
                if extension not in limits.supported_extensions:
                    warnings.append(f"Файл пропущен: {member.filename}")
                    continue

                image_members.append(member)

            if not image_members:
                raise DatasetImportError("ZIP не содержит поддерживаемых изображений")

            if len(image_members) > limits.max_images:
                raise DatasetImportError("В ZIP-архиве слишком много файлов")

            total_extracted_size = sum(
                member.file_size
                for member in image_members
            )
            if total_extracted_size > max_extracted_size_bytes:
                raise DatasetImportError(
                    "Распакованный размер изображений превышает допустимый лимит"
                )

            workspace = DatasetWorkspace(workspace_root, dataset_id)
            workspace.create()

            images = []
            shutil.copy2(zip_path, workspace.original_zip_path)

            for index, member in enumerate(image_members, start=1):
                image_id = f"image_{index:06d}"
                member_path = PurePosixPath(member.filename)
                extension = member_path.suffix.lower()
                stored_filename = f"{image_id}{extension}"
                target_path = workspace.image_dir / stored_filename

                with archive.open(member) as source:
                    with target_path.open("wb") as target:
                        shutil.copyfileobj(source, target)
                images.append(
                    ImageItem(
                        id=image_id,
                        filename=member.filename,
                        path=f"images/{stored_filename}",
                        width=0,
                        height=0,
                        approved=False,
                        viewed=False,
                    )
                )
            dataset = Dataset(
                id=dataset_id,
                name=dataset_name,
                status="READY",
                image_count=len(images),
                images=images,
                warnings=warnings,
            )
            workspace.save_metadata(dataset)
            return dataset

    except zipfile.BadZipFile as error:
        raise DatasetImportError(
            "ZIP-архив поврежден или имеет неверный формат"
        ) from error





