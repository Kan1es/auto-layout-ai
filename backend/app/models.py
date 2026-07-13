from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime

class Parameters(BaseModel):
    x: float
    y: float
    width: float
    height: float

class InternalImage(BaseModel):
    id: str
    filename: str
    width: int
    height: int

class AnnotationObj(BaseModel):
    label: str
    confidence: float
    bbox: Parameters | None
    mask: dict | list | None

class InternalAnnotation(BaseModel):
    image: InternalImage
    objects: list[AnnotationObj] = Field(default_factory=list)

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

class DatasetError(BaseModel):
    stage: str
    image_id: str | None = None
    filename: str | None = None
    message: str
    details: dict | list | str | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    
class DartSettings(BaseModel):
    prompt: str
    confidence: float
    mode: Literal["bbox", "mask", "bbox_and_mask"]
    show_overlay: bool
    updated_at: datetime = Field(default_factory=datetime.now)

class Annotation(BaseModel):
    image_id: str
    objects: list[AnnotationObj]


class RepresentativeInitRequest(BaseModel):
    target_count: int = Field(gt=0)


class RepresentativeState(BaseModel):
    target_count: int = Field(gt=0)
    history: list[str] = Field(default_factory=list)
    current_index: int = Field(default=-1, ge=-1)
    approved_image_ids: list[str] = Field(default_factory=list)


class RepresentativeImageResponse(BaseModel):
    id: str
    filename: str
    url: str
    width: int
    height: int
    approved: bool


class RepresentativeStateResponse(BaseModel):
    dataset_id: str
    target_count: int
    approved_count: int
    viewed_count: int
    total_count: int
    current_image: RepresentativeImageResponse | None
    can_go_prev: bool
    can_go_next: bool
    completed: bool