import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("omp_delegator_config", ROOT / "config.py")
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class Settings:
    def __init__(self, **values):
        self.values = values

    def get_config(self, key, default=None):
        return self.values.get(key, default)


class ConfigurationBehavior(unittest.TestCase):
    def test_missing_or_empty_repository_roots_are_rejected(self):
        for settings in (Settings(), Settings(allowed_roots=[])):
            with self.subTest(settings=settings.values):
                with self.assertRaises(ValueError):
                    CONFIG.load_config(settings)

    def test_relative_or_nonexistent_repository_roots_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for root in ("repository", str(Path(temporary) / "missing")):
                with self.subTest(root=root), self.assertRaises(ValueError):
                    CONFIG.load_config(Settings(allowed_roots=[root]))

    def test_invalid_timeout_cannot_start_a_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            for timeout in (False, 0, -1, float("inf"), float("nan"), "forever"):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    CONFIG.load_config(Settings(allowed_roots=[temporary], timeout=timeout))
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
