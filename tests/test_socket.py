"""Qualified sandboxed fixtures for the exact Unix health boundary."""
from contextlib import closing, contextmanager
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from sampler.firewalla_health import sampler, transport


NOW = 1_800_000_000


def reply():
    return json.dumps({
        "schema_version": 1, "operation": "get_network_status",
        "identity": "verified", "timestamp": transport.utc(NOW - 2), "uptime": 180,
    }).encode() + b"\n"


@contextmanager
def isolated_endpoint(payload, *, delay=0):
    with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="fwh-") as folder:
        path = Path(folder, "health.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            path.chmod(0o600)
            listener.listen(1)
            listener.settimeout(2)
            requests = []
            errors = []

            def server():
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(1)
                        with closing(conn.makefile("rb")) as source:
                            data = source.read(65)
                        requests.append(data)
                        if delay:
                            time.sleep(delay)
                        conn.sendall(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The client may abandon on its deadline or response cap.
                except OSError as error:
                    errors.append(error)

            thread = threading.Thread(target=server, daemon=True)
            thread.start()
            try:
                yield path, requests
            finally:
                thread.join(3)
                if thread.is_alive() or errors:
                    raise AssertionError("Fixture endpoint failed") from (errors[0] if errors else None)


class SocketTests(unittest.TestCase):
    def test_real_unix_peer_and_one_fixed_request(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with isolated_endpoint(reply()) as (path, requests):
                result = sampler.sample(
                    state, lambda: transport.local_status(path, os.getuid()), clock=lambda: NOW,
                    require_heap_limit=True,
                )
            self.assertEqual(result, "observed")
            self.assertEqual(requests, [b'{"schema_version":1,"operation":"get_network_status"}\n'])
            reopened = sampler.status(state, now=NOW)
            self.assertEqual(reopened["health"]["reported_uptime_s"], 180)

    def test_launch_entrypoint_uses_same_transport_and_private_store(self):
        with tempfile.TemporaryDirectory() as home:
            installed = Path(home, "Library", "Application Support", "Firewalla Health Sampler")
            installed.mkdir(parents=True, mode=0o700)
            root = Path(__file__).resolve().parents[1]
            payload = json.loads(reply())
            payload["timestamp"] = transport.utc(time.time() - 1)
            entry = root / "sampler" / "firewalla-health.py"
            child = (
                "from pathlib import Path; import os,pwd,sys; "
                "from sampler.firewalla_health import transport; "
                "transport.SOCKET_PATH=Path(sys.argv[1]); "
                "transport.OWNER_ACCOUNT=pwd.getpwuid(os.getuid()).pw_name; "
                "from sampler.firewalla_health.cli import main; raise SystemExit(main(['run']))"
            )
            env = {**os.environ, "HOME": home}
            with isolated_endpoint(json.dumps(payload).encode() + b"\n") as (path, requests):
                run = subprocess.run([sys.executable, "-E", "-s", "-B", "-c", child, str(path)],
                                     cwd=root, env=env, capture_output=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(len(requests), 1)
            status = subprocess.run([sys.executable, "-E", "-s", "-B", str(entry), "status"],
                                    cwd=root, env=env, capture_output=True, check=True, timeout=5)
            self.assertEqual(json.loads(status.stdout)["health"]["reported_uptime_s"], 180)
            self.assertEqual(status.stderr, b"")

    def test_oversize_and_malformed_are_rejected_at_the_socket(self):
        for payload, reason in (
            (b' ' * 16_384 + b'\n', "response_too_large"),
            (b'{"identity":"unconfirmed","identity":"verified"}\n', "invalid_response"),
            (reply() + reply(), "invalid_response"),
            (b'{"schema_version":\xff}\n', "invalid_response"),
        ):
            with self.subTest(reason=reason), isolated_endpoint(payload) as (path, _):
                with self.assertRaises(transport.ReadError) as error:
                    transport.local_status(path, os.getuid())
                self.assertEqual(error.exception.reason, reason)
        at_limit = b' ' * (16_384 - len(reply())) + reply()
        with isolated_endpoint(at_limit) as (path, _):
            self.assertEqual(transport.local_status(path, os.getuid())["uptime"], 180)

    def test_deadline_and_recovery(self):
        with isolated_endpoint(reply(), delay=0.2) as (path, _):
            before = time.monotonic()
            with self.assertRaises(transport.ReadError) as error:
                transport.local_status(path, os.getuid(), deadline_seconds=0.05)
            elapsed = time.monotonic() - before
            self.assertLess(elapsed, 0.2)
            self.assertEqual(error.exception.reason, "timeout")
        with isolated_endpoint(reply()) as (path, _):
            self.assertEqual(transport.local_status(path, os.getuid())["identity"], "verified")

    def test_refuses_path_symlink_or_wrong_owner_without_a_connection(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="fwh-") as folder:
            path = Path(folder, "health.sock")
            link = Path(folder, "link.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                path.chmod(0o600)
                listener.listen(1)
                link.symlink_to(path)
                for socket_path, uid in ((link, os.getuid()), (path, os.getuid() + 1)):
                    with self.assertRaises(transport.ReadError) as error:
                        transport.local_status(socket_path, uid)
                    self.assertEqual(error.exception.reason, "identity_unverified")
                listener.setblocking(False)
                with self.assertRaises(BlockingIOError):
                    listener.accept()
