from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw


DEMO_IMAGES = (
    ("red_box.jpg", "red", (205, 55, 55), (45, 35, 195, 125)),
    ("green_box.jpg", "green", (55, 160, 85), (35, 25, 180, 115)),
    ("blue_box.jpg", "blue", (55, 95, 200), (55, 40, 210, 135)),
)


def build_demo_zip(output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(output_path, "w", ZIP_DEFLATED) as archive:
        for filename, label, color, rectangle in DEMO_IMAGES:
            image = Image.new("RGB", (256, 160), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle(rectangle, fill=color, outline="black", width=3)
            draw.text((10, 10), f"{label} box", fill="black")

            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=92)
            archive.writestr(f"kan-174-demo/{filename}", buffer.getvalue())

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Создать маленький ZIP-датасет для smoke test KAN-174."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("workspace/smoke/kan-174-demo.zip"),
    )
    args = parser.parse_args()

    output_path = build_demo_zip(args.output)
    print(output_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
