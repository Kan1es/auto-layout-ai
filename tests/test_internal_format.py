import unittest
from pydantic import ValidationError
from backend.app.models import InternalAnnotation

class InternalFormatTest(unittest.TestCase):
    def test_internal_annotation_contains_required_fields(self):
        annotation = InternalAnnotation.model_validate(
            {
                "image": {
                    "id": "image_000001",
                    "filename": "board.jpg",
                    "width": 1920,
                    "height": 1080,
                },
                "objects": [
                    {
                        "label": "wood board",
                        "confidence": 0.87,
                        "bbox": {
                            "x": 120,
                            "y": 80,
                            "width": 40,
                            "height": 35,
                        },
                        "mask": None,
                    }
                ],
            }
        )

        self.assertEqual(annotation.image.id, "image_000001")
        self.assertEqual(annotation.image.filename, "board.jpg")
        self.assertEqual(annotation.image.width, 1920)
        self.assertEqual(annotation.image.height, 1080)
        self.assertEqual(annotation.objects[0].label, "wood board")
        self.assertEqual(annotation.objects[0].confidence, 0.87)
        self.assertEqual(annotation.objects[0].bbox.x, 120)
        self.assertIsNone(annotation.objects[0].mask)

    def test_internal_annotation_requires_image_block(self):
        with self.assertRaises(ValidationError):
            InternalAnnotation.model_validate(
                {
                    "objects": [],
                }
            )

if __name__ == "__main__":
    unittest.main()