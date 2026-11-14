"""Public CLI, launch definition and runtime constants."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

from sampler.firewalla_health import observer, transport


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000


class RuntimeTests(unittest.TestCase):
    def test_observer_context_contains_counts_only_and_expires(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder, "cache.json")
            cache.write_text(json.dumps({
                "schema_version": 1, "collected_at": "2027-01-15T07:59:55+00:00",
                "fresh_until": "2027-01-15T08:09:55+00:00", "sample_interval_seconds": 300,
                "targets": [{"up":1,"status":"healthy","id":"PRIVATE-01"} for _ in range(4)],
            }))
            cache.chmod(0o600)
            self.assertEqual(observer.context(cache, NOW), {
                "state": "observed", "responding": 4, "qualified": 4,
                "observed_at":"2027-01-15T07:59:55Z",
            })
            self.assertEqual(observer.context(cache, NOW + 600), {"state":"unknown"})
            link = Path(folder, "linked.json")
            link.symlink_to(cache)
            self.assertEqual(observer.context(link, NOW), {"state":"unknown"})
            cache.chmod(0o644)
            self.assertEqual(observer.context(cache, NOW), {"state":"unknown"})

    def test_launchagent_has_fixed_health_command_and_schedule(self):
        manifest = ROOT / "deploy" / "com.nibrwr.firewalla-security-sampler.plist.in"
        with manifest.open("rb") as source:
            agent = plistlib.load(source)
        self.assertEqual(agent["Label"], "com.nibrwr.firewalla-security-sampler")
        self.assertEqual(agent["StartInterval"], 300)
        self.assertEqual(agent["ProgramArguments"], [
            "/opt/homebrew/bin/python3", "-E", "-s", "-B", "@SAMPLER_HOME@/firewalla-health.py", "run",
        ])
        self.assertEqual(agent["StandardOutPath"], "/dev/null")
        self.assertEqual(agent["StandardErrorPath"], "/dev/null")
        self.assertNotIn("KeepAlive", agent)
        self.assertNotIn("EnvironmentVariables", agent)

    def test_status_is_read_only_when_state_is_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            env = {**os.environ, "HOME": str(home)}
            run = subprocess.run(
                [sys.executable, "-E", "-s", "-B", str(ROOT / "sampler" / "firewalla-health.py"), "status"],
                env=env, check=True, capture_output=True, timeout=5, text=True,
            )
            self.assertEqual(json.loads(run.stdout)["health"], {"state":"unknown","reason":"no_samples"})
            self.assertEqual(list(home.iterdir()), [])
            self.assertEqual(run.stderr, "")

    def test_missing_socket_refuses_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(transport.ReadError) as error:
                transport.local_status(Path(folder, "missing.sock"), os.getuid())
            self.assertEqual(error.exception.reason, "endpoint_unavailable")


if __name__ == "__main__":
    unittest.main()
