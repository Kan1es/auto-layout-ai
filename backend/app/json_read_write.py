import json
import os
import tempfile
from pydantic import BaseModel
from pathlib import Path

from .errors import (
    JsonFormatError,
    JsonNotFoundError,
    JsonReadError,
    JsonWriteError,
)

def read_json(path):
    path = Path(path)

    if not path.exists():
        raise JsonNotFoundError(path)

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    except json.JSONDecodeError as error:
        raise JsonFormatError(
            path,
            error.lineno
        )
    except OSError as error:
        raise JsonReadError(
            path,
            error
        )

def write_json(path, data):
    path = Path(path)
    temp_path = None
    try:
        path.parent.mkdir(
            parents = True,
            exist_ok = True
        )
        if isinstance(data, BaseModel):
            data = data.model_dump(mode = "json")

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)
            json.dump(
                data,
                file,
                ensure_ascii = False,
                indent = 2
            )
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, path)

    except (OSError, TypeError, ValueError) as error:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise JsonWriteError(
            path,
            error
        )
