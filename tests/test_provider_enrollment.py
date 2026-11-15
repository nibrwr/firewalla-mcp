"""Attested input never becomes a raw state-file field or new credential."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "provider_attest", Path(__file__).resolve().parents[1] / "provider" / "attest.py"
)
attest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attest)


class EnrollmentTests(unittest.TestCase):
    def test_independent_target_match_creates_one_private_pinned_record(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(attest, "STATE", Path(folder)), \
                patch.object(attest, "protected_target", return_value="192.168.255.6"), \
                patch("pwd.getpwuid") as lookup:
            lookup.return_value.pw_uid = os.getuid() + 1
            answers = iter(["fixture-group-reference", "192.168.255.6"])
            attest.enroll(os.getuid() + 1, hidden=lambda _: next(answers))
            path = Path(folder, "identity.json")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            record = path.read_text()
            self.assertNotIn("fixture-group-reference", record)
            self.assertNotIn("192.168.255.6", record)
            self.assertEqual(json.loads(record)["schema_version"], 1)
            changed = iter(["different-fixture-group", "192.168.255.6"])
            with self.assertRaises(FileExistsError):
                attest.enroll(os.getuid() + 1, hidden=lambda _: next(changed))
            self.assertEqual(path.read_text(), record)

    def test_mismatched_target_never_creates_a_pin(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(attest, "STATE", Path(folder)), \
                patch.object(attest, "protected_target", return_value="192.168.255.6"), \
                patch("pwd.getpwuid") as lookup:
            lookup.return_value.pw_uid = os.getuid() + 1
            answers = iter(["fixture-group-reference", "192.168.255.7"])
            with self.assertRaises(ValueError):
                attest.enroll(os.getuid() + 1, hidden=lambda _: next(answers))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_disk_write_failure_leaves_no_pin_and_admits_one_fresh_enrollment(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(attest, "STATE", Path(folder)), patch.object(
            attest, "protected_target", return_value="192.168.255.6"
        ), patch("pwd.getpwuid") as lookup:
            lookup.return_value.pw_uid = os.getuid() + 1

            def enroll():
                entries = iter(["fixture-group-reference", "192.168.255.6"])
                attest.enroll(os.getuid() + 1, hidden=lambda _: next(entries))

            with patch.object(attest.os, "fsync", side_effect=OSError("fixture disk full")):
                with self.assertRaises(OSError):
                    enroll()
            self.assertFalse((Path(folder) / "identity.json").exists())
            enroll()
            self.assertTrue((Path(folder) / "identity.json").is_file())

    def test_directory_sync_failure_retries_without_replacing_the_published_pin(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(attest, "STATE", Path(folder)), patch.object(
            attest, "protected_target", return_value="192.168.255.6"
        ), patch("pwd.getpwuid") as lookup:
            lookup.return_value.pw_uid = os.getuid() + 1

            def enroll():
                entries = iter(["fixture-group-reference", "192.168.255.6"])
                attest.enroll(os.getuid() + 1, hidden=lambda _: next(entries))

            with patch.object(attest.os, "fsync", side_effect=[None, OSError("fixture directory sync")]):
                with self.assertRaises(OSError):
                    enroll()
            pin = Path(folder, "identity.json")
            published = pin.stat()
            self.assertEqual(list(Path(folder).iterdir()), [pin])
            enroll()
            self.assertEqual(pin.stat().st_ino, published.st_ino)
            self.assertEqual(pin.stat().st_mtime_ns, published.st_mtime_ns)
