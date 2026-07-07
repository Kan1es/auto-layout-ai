import json
from pydantic import BaseModel
from pathlib import Path

from .errors import (
    JsonFormatError,
    JsonNotFountError,
    JsonReadError,
    JsonWriteError,
)

def read_json(path):
    path = Path(path)

    if not path.exists():
        raise JsonNotFountError(path)

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
    try:
        path.parent.mkdir(
            parents = True,
            exist_ok = True
        )
        if isinstance(data, BaseModel):
            data = data.model_dump(mode = "json")

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii = False,
                indent = 2
            )

    except (OSError, TypeError, ValueError) as error:
        raise JsonWriteError(
            path,
            error
        )