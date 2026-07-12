from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime

class Parameters(BaseModel):
    x: float
    y: float
    width: float
    height: float

class AnnotationObj(BaseModel):
    label: str
    confidence: float
    bbox: Parameters | None
    mask: dict | list | None

class ImageItem(BaseModel):
    id: str
    filename: str
    path: str
    width: int | None
    height: int | None
    approved: bool = False
    viewed: bool = False
    readable: bool = True

class Dataset(BaseModel):
    id: str
    name: str
    status: Literal[
        "PROCESSING",
        "READY",
        "FAILED"
    ]
    image_count: int
    images: list[ImageItem]
    created_at: datetime = Field(default_factory=datetime.now)
    warnings: list[str] = Field(default_factory=list)


class DartSettings(BaseModel):
    prompt: str
    confidence: float
    mode: Literal["bbox", "mask", "bbox_and_mask"]
    show_overlay: bool
    updated_at: datetime = Field(default_factory=datetime.now)

class Annotation(BaseModel):
    image_id: str
    objects: list[AnnotationObj]
