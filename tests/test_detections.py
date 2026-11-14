"""Run fictional evidence through the same offline entry point operators use."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "sampler" / "evaluate-fixtures.py"
FIXTURES = ROOT / "tests" / "fixtures"


class FixtureTests(unittest.TestCase):
    def check_cases(self, name):
        path = FIXTURES / name
        expected = [x["expected"] for x in json.loads(path.read_text())]
        run = subprocess.run(
            [sys.executable, "-B", str(DRIVER), str(path)],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), expected)
        for marker in ("ALARM-", "DEVICE-", "RULE-", "SCOPE-", "VLAN-", "SEGMENT-", "PRIVATE-"):
            self.assertNotIn(marker, run.stdout)

    def test_development_cases(self):
        self.check_cases("development.json")

    def test_held_out_cases(self):
        self.check_cases("held-out.json")

    def test_invalid_file_has_sanitized_error(self):
        run = subprocess.run(
            [sys.executable, "-B", str(DRIVER), str(ROOT / "README.md")],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout), {"state":"unknown","reason":"invalid_fixture_file"})
        self.assertEqual(run.stderr, "")
