"""Create a small local image used to smoke-test the DART command line script."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT_DIR / "samples" / "test.jpg"


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGB", (960, 640), "#edf3f8")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((140, 160, 820, 480), radius=36, fill="#1d4ed8")
    draw.ellipse((245, 245, 375, 375), fill="#fbbf24")
    draw.rectangle((445, 235, 710, 405), fill="#f8fafc")
    draw.line((180, 510, 780, 510), fill="#94a3b8", width=10)

    image.save(OUTPUT_PATH, format="JPEG", quality=92)
    print(f"Sample image created: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
