import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "qwen3_batch_tts.py"
SPEC = importlib.util.spec_from_file_location("qwen3_batch_tts", MODULE_PATH)
qwen3_batch_tts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qwen3_batch_tts)


class ConcatenateOutputsTests(unittest.TestCase):
    def test_inserts_configured_pause_between_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            manifest = {
                "chunks": [
                    {"index": 1, "status": "success", "output_path": str(tmp_path / "001.flac")},
                    {"index": 2, "status": "success", "output_path": str(tmp_path / "002.flac")},
                    {"index": 3, "status": "success", "output_path": str(tmp_path / "003.flac")},
                ]
            }

            with patch.object(subprocess, "run") as run:
                target = qwen3_batch_tts.concatenate_outputs(tmp_path, manifest, pause_ms=500)

        self.assertEqual(target, tmp_path / "combined.flac")
        command = run.call_args.args[0]
        self.assertEqual(command.count("-i"), 3)
        filter_index = command.index("-filter_complex") + 1
        self.assertIn("anullsrc=r=24000:cl=mono:d=0.500", command[filter_index])
        self.assertIn("concat=n=5:v=0:a=1[outa]", command[filter_index])
        self.assertIn("-map", command)
        self.assertIn("[outa]", command)


if __name__ == "__main__":
    unittest.main()
