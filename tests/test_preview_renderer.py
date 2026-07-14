from pathlib import Path
import tempfile
import unittest

from PIL import Image

from backend.app.preview_renderer import PreviewRenderer


class PreviewRendererTest(unittest.TestCase):
    def test_renders_bbox_label_and_confidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "board.jpg"
            output_path = root / "results" / "previews" / "board_preview.jpg"

            Image.new("RGB", (100, 100), "white").save(image_path)

            annotation = {
                "image_id": "image_000001",
                "objects": [
                    {
                        "label": "wood board",
                        "confidence": 0.87,
                        "bbox": {
                            "x": 10,
                            "y": 10,
                            "width": 50,
                            "height": 40,
                        },
                        "mask": None,
                    }
                ],
            }

            result = PreviewRenderer().render(
                image_path=image_path,
                annotation=annotation,
                output_path=output_path,
                preview_url="/workspace/datasets/ds_001/results/previews/board_preview.jpg",
            )

            self.assertTrue(output_path.exists())
            self.assertEqual(result["objects_count"], 1)
            self.assertEqual(
                result["preview_url"],
                "/workspace/datasets/ds_001/results/previews/board_preview.jpg",
            )
            self.assertTrue(result["preview_only"])

    def test_renders_polygon_mask_when_mask_is_available(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "board.jpg"
            output_path = root / "preview.jpg"

            Image.new("RGB", (100, 100), "white").save(image_path)

            annotation = {
                "image_id": "image_000001",
                "objects": [
                    {
                        "label": "wood board",
                        "confidence": 0.87,
                        "bbox": None,
                        "mask": {
                            "polygon": [
                                [10, 10],
                                [80, 10],
                                [80, 80],
                                [10, 80],
                            ]
                        },
                    }
                ],
            }

            PreviewRenderer().render(
                image_path=image_path,
                annotation=annotation,
                output_path=output_path,
            )

            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()