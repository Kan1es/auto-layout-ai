from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from backend.app.dart_runner import (
    DartRunner,
    DartRunnerTimeout,
    DartRunnerUnsupportedMode,
)

class DartRunnerTest(unittest.TestCase):
    def test_run_image_returns_raw_and_normalized_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script_path = root / "fake_dart.py"
            image_path = root / "image.jpg"
            config_path = root / "GroundingDINO_SwinT_OGC.py"
            checkpoint_path = root / "groundingdino_swint_ogc.pth"
            output_root = root / "runs"

            image_path.write_bytes(b"fake image")
            config_path.write_text("# fake config", encoding="utf-8")
            checkpoint_path.write_bytes(b"fake checkpoint")
            script_path.write_text(
                textwrap.dedent(
                    r'''
                    import argparse
                    import json
                    from pathlib import Path

                    parser = argparse.ArgumentParser()
                    parser.add_argument("--image-path", required=True)
                    parser.add_argument("--prompt", required=True)
                    parser.add_argument("--confidence", required=True)
                    parser.add_argument("--mode", required=True)
                    parser.add_argument("--config-path", required=True)
                    parser.add_argument("--checkpoint-path", required=True)
                    parser.add_argument("--output-dir", required=True)
                    parser.add_argument("--text-threshold", required=True)
                    parser.add_argument("--device", required=True)
                    args = parser.parse_args()

                    output_dir = Path(args.output_dir)
                    output_dir.mkdir(parents=True, exist_ok=True)
                    raw = {
                        "prompt": args.prompt,
                        "confidence": float(args.confidence),
                        "mode": args.mode,
                        "groundingdino": {"boxes_cxcywh_normalized": []},
                    }
                    normalized = {
                        "image": {"id": "image", "filename": "image.jpg", "width": 10, "height": 20},
                        "objects": [],
                        "settings": {
                            "prompt": args.prompt,
                            "confidence": float(args.confidence),
                            "mode": args.mode,
                        },
                    }
                    (output_dir / "raw_result.json").write_text(json.dumps(raw), encoding="utf-8")
                    (output_dir / "normalized_result.json").write_text(json.dumps(normalized), encoding="utf-8")
                    (output_dir / "preview.jpg").write_bytes(b"fake preview")
                    '''
                ),
                encoding="utf-8"
            )

            runner = DartRunner(
                script_path=script_path,
                config_path=config_path,
                checkpoint_path=checkpoint_path,
                output_root=output_root,
                python_executable=sys.executable,
                timeout_seconds=5,
            )
            result = runner.run_image(image_path, "bolt", 0.35, "bbox")

        self.assertEqual(result.raw_result["prompt"], "bolt")
        self.assertEqual(result.normalized_result["settings"]["mode"], "bbox")
        self.assertTrue(result.raw_result_path.name.endswith("raw_result.json"))
        self.assertEqual(result.preview_path.name, "preview.jpg")

    def test_rejects_unsupported_mode_before_starting_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "image.jpg"
            script_path = root / "fake_dart.py"
            config_path = root / "config.py"
            checkpoint_path = root / "model.pth"
            image_path.write_bytes(b"fake image")
            script_path.write_text("print('should not run')", encoding="utf-8")
            config_path.write_text("# fake config", encoding="utf-8")
            checkpoint_path.write_bytes(b"fake checkpoint")

            runner = DartRunner(
                script_path=script_path,
                config_path=config_path,
                checkpoint_path=checkpoint_path,
                python_executable=sys.executable
            )

            with self.assertRaises(DartRunnerUnsupportedMode):
                runner.run_image(image_path, "bolt", 0.35, "mask")

    def test_raises_timeout_when_process_is_too_slow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "image.jpg"
            script_path = root / "slow_dart.py"
            config_path = root / "config.py"
            checkpoint_path = root / "model.pth"
            image_path.write_bytes(b"fake image")
            config_path.write_text("# fake config", encoding="utf-8")
            checkpoint_path.write_bytes(b"fake checkpoint")
            script_path.write_text("import time; time.sleep(5)", encoding="utf-8")

            runner = DartRunner(
                script_path=script_path,
                config_path=config_path,
                checkpoint_path=checkpoint_path,
                output_root=root / "runs",
                python_executable=sys.executable,
                timeout_seconds=1
            )
            with self.assertRaises(DartRunnerTimeout):
                runner.run_image(image_path, "bolt", 0.35, "bbox")

if __name__ == "__main__":
    unittest.main()