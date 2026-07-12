from pathlib import Path
from .json_read_write import read_json, write_json

from .models import (
    Annotation,
    DartSettings,
    Dataset,
    DatasetError
)

class DatasetWorkspace:
    def __init__(self, root, id):
        if not id:
            raise ValueError("id для датасета не должно быть пустым")

        self.root = Path(root)
        self.id = id

        self.dataset_dir = (
            self.root / "datasets" / id
        )

        self.image_dir = self.dataset_dir / "images"
        self.results_dir = self.dataset_dir / "results"
        self.raw_dir = self.results_dir / "raw"
        self.previews_dir = self.results_dir / "previews"
        self.metadata_path = self.dataset_dir / "metadata.json"
        self.selected_images_path = (
                self.dataset_dir / "selected_images.json"
        )
        self.dart_settings_path = (
                self.dataset_dir / "dart_settings.json"
        )
        self.annotations_path = (
                self.results_dir / "annotations_internal.json"
        )
        self.errors_path = self.results_dir / "errors.json"

        self.upload_dir = self.dataset_dir / "upload"
        self.original_zip_path = self.upload_dir / "original.zip"

    def create(self):
        self.image_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.raw_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.previews_dir.mkdir(
            parents=True,
            exist_ok=True
        )
        
        self.upload_dir.mkdir(
            parents=True, 
            exist_ok=True
        )

    def save_metadata(self, dataset):
        if not isinstance(dataset, Dataset):
            raise TypeError("Ожидалась модель Dataset")

        write_json(
            self.metadata_path,
            dataset
        )

    def load_metadata(self):
        data = read_json(self.metadata_path)
        return Dataset.model_validate(data)

    def save_selected_images(self, image_ids):
        write_json(
            self.selected_images_path,
            {
                "image_ids": image_ids
            }
        )

    def load_selected_images(self):
        data = read_json(self.selected_images_path)
        return data.get("image_ids", [])

    def save_dart_settings(self, settings):
        if not isinstance(settings, DartSettings):
            raise TypeError("Ожидалась модель DartSettings")

        write_json(
            self.dart_settings_path,
            settings
        )

    def load_dart_settings(self):
        data = read_json(self.dart_settings_path)
        return DartSettings.model_validate(data)

    def save_annotations(self, annotations):
        data = {
            "annotations": [
                annotation.model_dump(mode="json")
                for annotation in annotations
            ]
        }

        write_json(
            self.annotations_path,
            data
        )

    def load_annotations(self):
        data = read_json(self.annotations_path)

        return[
            Annotation.model_validate(annotation)
            for annotation in data.get("annotations", [])
        ]

    def save_errors(self, errors):
        write_json(
            self.errors_path,
            {
                "errors": [
                    error.model_dump(mode="json")
                    if isinstance(error, DatasetError)
                    else error
                    for error in errors
                ]
            }
        )

    def load_errors(self):
        if not self.errors_path.exists():
            return []

        data = read_json(self.errors_path)
        return [
            DatasetError.model_validate(error)
            for error in data.get("errors", [])
        ]

    def append_error(self, error):
        if not isinstance(error, DatasetError):
            error = DatasetError.model_validate(error)

        errors = self.load_errors()
        errors.append(error)
        self.save_errors(errors)

    def save_raw_result(self, image_id, result):
        path = self.raw_dir / f"{image_id}.json"
        write_json(path, result)

    def load_raw_result(self, image_id):
        path = self.raw_dir / f"{image_id}.json"
        return read_json(path)
