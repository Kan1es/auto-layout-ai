# Internal Annotation Format

Internal format is the normalized annotation format used between DART, preview rendering, autolabeling, and CVAT export.

Preview images are not annotation results. They are only visual overlays for manual checking.

## Schema

```json
{
  "image": {
    "id": "image_000001",
    "filename": "board.jpg",
    "width": 1920,
    "height": 1080
  },
  "objects": [
    {
      "label": "wood board",
      "confidence": 0.87,
      "bbox": {
        "x": 120,
        "y": 80,
        "width": 40,
        "height": 35
      },
      "mask": null
    }
  ]
}
```

```

## Fields

- `image.id` - stable internal image id.
- `image.filename` - original or workspace image filename.
- `image.width` - image width in pixels.
- `image.height` - image height in pixels.
- `objects[].label` - detected object label.
- `objects[].confidence` - confidence score from 0 to 1.
- `objects[].bbox` - bounding box in absolute pixels.
- `objects[].mask` - mask data or `null` for bbox-only mode.

## Bbox

`bbox` uses absolute pixel coordinates:

```json
{
  "x": 120,
  "y": 80,
  "width": 40,
  "height": 35
}
```