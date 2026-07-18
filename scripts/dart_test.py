from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

SUPPORTED_MODES = {"bbox", "mask", "bbox_and_mask"}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local DART on one image and save raw, normalized and preview output."
    )
    parser.add_argument("--image-path", required=True, help="Path to input image.")
    parser.add_argument("--prompt", required=True, help="Text prompt, for example: bolt")
    parser.add_argument("--confidence", type=float, default=0.35, help="Box confidence threshold.")
    parser.add_argument(
        "--mode",
        default="bbox",
        choices=sorted(SUPPORTED_MODES),
        help="Annotation mode. MVP script supports bbox first.",
    )
    parser.add_argument(
        "--config-path",
        required=True,
        help="Path to GroundingDINO config, for example GroundingDINO_SwinT_OGC.py.",
    )
    parser.add_argument(
        "--checkpoint-path",
        required=True,
        help="Path to GroundingDINO checkpoint, for example groundingdino_swint_ogc.pth.",
    )
    parser.add_argument(
        "--output-dir",
        default="workspace/dart_single_image",
        help="Directory where JSON and preview files will be saved.",
    )
    parser.add_argument(
        "--text-threshold",
        type=float,
        default=0.25,
        help="GroundingDINO text threshold.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "cuda"],
        help="Device for GroundingDINO inference.",
    )
    return parser.parse_args()

def ensure_bbox_mode(mode):
    if mode != "bbox":
        raise SystemExit(
            "Only mode=bbox is implemented in this standalone MVP check. "
            "Mask mode should be added after the DART/SAM mask path is fixed."
        )

def load_groundingdino():
    try:
        from groundingdino.util.inference import annotate, load_image, load_model, predict
    except ImportError as error:
        raise SystemExit(
            "GroundingDINO is not importable. Install DART/GroundingDINO dependencies first. "
            "Expected import: groundingdino.util.inference."
        ) from error

    return annotate, load_image, load_model, predict

def xyxy_to_bbox(box):
    x1, y1, x2, y2 = box
    return {
        "x": round(float(x1), 4),
        "y": round(float(y1), 4),
        "width": round(float(x2 - x1), 4),
        "height": round(float(y2 - y1), 4),
    }

def cxcywh_norm_to_xyxy_pixels(
    box: Any,
    image_width: int,
    image_height: int
):
    cx, cy, width, height = [float(value) for value in box]
    x1 = (cx - width / 2) * image_width
    y1 = (cy - height / 2) * image_height
    x2 = (cx + width / 2) * image_width
    y2 = (cy + height / 2) * image_height

    return[
        max(0.0, min(float(image_width), x1)),
        max(0.0, min(float(image_height), y1)),
        max(0.0, min(float(image_width), x2)),
        max(0.0, min(float(image_height), y2)),
    ]

def tensor_to_list(value):
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def draw_preview(
    image_path: Path,
    objects: list[dict[str, Any]],
    output_path: Path,
):
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()

    for item in objects:
        bbox = item["bbox"]
        x1 = bbox["x"]
        y1 = bbox["y"]
        x2 = bbox["x"] + bbox["width"]
        y2 = bbox["y"] + bbox["height"]

        draw.rectangle((x1, y1, x2, y2), outline="red", width=3)

        label = f'{item["label"]} {item["confidence"]:.2f}'
        text_box = draw.textbbox((x1, y1), label, font=font)
        draw.rectangle(text_box, fill="red")
        draw.text((x1, y1), label, fill="white", font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=92)


def main():
    args = parse_args()
    ensure_bbox_mode(args.mode)

    image_path = Path(args.image_path).resolve()
    config_path = Path(args.config_path).resolve()
    checkpoint_path = Path(args.checkpoint_path).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not image_path.exists():
        raise SystemExit(f"Image not found: {image_path}")
    if not config_path.exists():
        raise SystemExit(f"GroundingDINO config not found: {config_path}")
    if not checkpoint_path.exists():
        raise SystemExit(f"GroundingDINO checkpoint not found: {checkpoint_path}")

    annotate, load_image, load_model, predict = load_groundingdino()

    model = load_model(str(config_path), str(checkpoint_path), device=args.device)
    image_source, image_tensor = load_image(str(image_path))

    boxes, logits, phrases = predict(
        model=model,
        image=image_tensor,
        caption=args.prompt,
        box_threshold=args.confidence,
        device=args.device,
        text_threshold=args.text_threshold
    )

    with Image.open(image_path) as image:
        image_width, image_height = image.size

    raw_boxes = tensor_to_list(boxes)
    raw_logits = tensor_to_list(logits)
    raw_phrases = list(phrases)

    raw_result = {
        "image_path": str(image_path),
        "prompt": args.prompt,
        "confidence": args.confidence,
        "mode": args.mode,
        "groundingdino": {
            "boxes_cxcywh_normalized": raw_boxes,
            "logits": raw_logits,
            "phrases": raw_phrases,
        },
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }

    objects = []
    for index, box in enumerate(raw_boxes):
        xyxy = cxcywh_norm_to_xyxy_pixels(box, image_width, image_height)
        objects.append(
            {
                "label": raw_phrases[index] if index < len(raw_phrases) else args.prompt,
                "confidence": round(float(raw_logits[index]), 6) if index < len(raw_logits) else 0.0,
                "bbox": xyxy_to_bbox(xyxy),
                "mask": None
            }
        )

    normalized_result = {
        "image": {
            "id": image_path.stem,
            "filename": image_path.name,
            "width": image_width,
            "height": image_height
        },
        "objects": objects,
        "settings": {
            "prompt": args.prompt,
            "confidence": args.confidence,
            "mode": args.mode
        }
    }

    raw_path = output_dir / "raw_result.json"
    normalized_path = output_dir / "normalized_result.json"
    preview_path = output_dir / "preview.jpg"

    save_json(raw_path, raw_result)
    save_json(normalized_path, normalized_result)
    draw_preview(image_path, objects, preview_path)

    print(json.dumps({
        "status": "OK",
        "objects_count": len(objects),
        "raw_result": str(raw_path),
        "normalized_result": str(normalized_path),
        "preview": str(preview_path)
    }, ensure_ascii=False, indent=2))

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
