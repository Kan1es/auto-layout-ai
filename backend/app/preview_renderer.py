from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


class PreviewRendererError(RuntimeError):
    pass


class PreviewRenderer:
    def render(
        self,
        *,
        image_path: Path | str,
        annotation: Any,
        output_path: Path | str,
        preview_url: str | None = None,
    ) -> dict[str, Any]:
        image_path = Path(image_path)
        output_path = Path(output_path)

        if not image_path.exists():
            raise PreviewRendererError(f"Image not found: {image_path}")

        data = self._to_dict(annotation)
        objects = data.get("objects", [])

        image = Image.open(image_path).convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)

        for item in objects:
            self._draw_mask(overlay_draw, item.get("mask"))

        image = Image.alpha_composite(image, overlay).convert("RGB")
        draw = ImageDraw.Draw(image)
        font = self._load_font()

        for item in objects:
            self._draw_bbox(draw, item, font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, quality=92)

        return {
            "preview_path": str(output_path),
            "preview_url": preview_url,
            "objects_count": len(objects),
            "preview_only": True,
        }

    def _to_dict(self, value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return value
        raise PreviewRendererError("Annotation must be a dict or Pydantic model.")

    def _load_font(self):
        try:
            return ImageFont.truetype("arial.ttf", 16)
        except OSError:
            return ImageFont.load_default()

    def _draw_bbox(self, draw: ImageDraw.ImageDraw, item: dict[str, Any], font) -> None:
        bbox = item.get("bbox")
        if not bbox:
            return

        x1 = float(bbox["x"])
        y1 = float(bbox["y"])
        x2 = x1 + float(bbox["width"])
        y2 = y1 + float(bbox["height"])

        draw.rectangle((x1, y1, x2, y2), outline="red", width=3)

        label = item.get("label", "object")
        confidence = item.get("confidence")
        if confidence is not None:
            text = f"{label} {float(confidence):.2f}"
        else:
            text = label

        text_box = draw.textbbox((x1, y1), text, font=font)
        draw.rectangle(text_box, fill="red")
        draw.text((x1, y1), text, fill="white", font=font)

    def _draw_mask(self, draw: ImageDraw.ImageDraw, mask: Any) -> None:
        for polygon in self._mask_polygons(mask):
            if len(polygon) >= 3:
                draw.polygon(
                    polygon,
                    fill=(255, 0, 0, 80),
                    outline=(255, 0, 0, 160),
                )

    def _mask_polygons(self, mask: Any) -> list[list[tuple[float, float]]]:
        if not mask:
            return []

        raw_polygons = []

        if isinstance(mask, dict):
            if "polygons" in mask:
                raw_polygons = mask["polygons"]
            elif "polygon" in mask:
                raw_polygons = [mask["polygon"]]
            elif "points" in mask:
                raw_polygons = [mask["points"]]
            elif "segmentation" in mask:
                segmentation = mask["segmentation"]
                raw_polygons = segmentation if self._is_list_of_lists(segmentation) else [segmentation]
        elif isinstance(mask, list):
            raw_polygons = mask if self._is_list_of_lists(mask) else [mask]

        return [
            polygon
            for polygon in (self._normalize_polygon(raw) for raw in raw_polygons)
            if polygon
        ]

    def _is_list_of_lists(self, value: Any) -> bool:
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, list) for item in value)
        )

    def _normalize_polygon(self, value: Any) -> list[tuple[float, float]]:
        if not isinstance(value, list) or not value:
            return []

        if all(isinstance(point, dict) for point in value):
            return [
                (float(point["x"]), float(point["y"]))
                for point in value
                if "x" in point and "y" in point
            ]

        if all(isinstance(point, list) and len(point) >= 2 for point in value):
            return [
                (float(point[0]), float(point[1]))
                for point in value
            ]

        if all(isinstance(number, (int, float)) for number in value) and len(value) % 2 == 0:
            return [
                (float(value[index]), float(value[index + 1]))
                for index in range(0, len(value), 2)
            ]

        return []