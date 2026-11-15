"""Fixed local health operation. Pairing credentials belong to the endpoint owner."""
import ctypes
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import socket
import stat
import sys
import time


SOCKET_PATH = Path("/var/run/firewalla-health/status.sock")
OWNER_ACCOUNT = "hermes"
MAX_RESPONSE = 16 * 1024
DEADLINE_SECONDS = 10
FRESH_SECONDS = 600
REQUEST = b'{"schema_version":1,"operation":"get_network_status"}\n'
TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", re.ASCII)
REPLY_FIELDS = {"schema_version", "operation", "identity", "timestamp", "uptime"}


class ReadError(Exception):
    """Reason is an internal enum, never copied from IPC or exception text."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


READ_FAILURES = {
    "endpoint_unavailable", "identity_unverified", "timeout", "response_too_large",
    "invalid_response", "stale", "future", "replayed", "clock_rollback", "resource_limit",
    "auth_backoff", "auth_unavailable", "read_limit", "source_denied", "source_unavailable",
    "storage_unavailable", "clock_invalid", "configuration",
}


def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def epoch(value):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ValueError("Unsupported timestamp")
    base, _, fraction = value[:-1].partition(".")
    moment = datetime.fromisoformat(base).replace(tzinfo=timezone.utc)
    return moment.timestamp() + int(fraction.ljust(6, "0")) / 1_000_000


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Repeated JSON member")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("Nonfinite JSON number")


def parse_json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)


def _peer_uid(connection):
    if sys.platform != "darwin":
        raise ReadError("endpoint_unavailable")
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    getpeereid = ctypes.CDLL(None).getpeereid
    getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    getpeereid.restype = ctypes.c_int
    if getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise ReadError("identity_unverified")
    return uid.value


def local_status(path, owner_uid, *, deadline_seconds=DEADLINE_SECONDS):
    """One request, one newline-terminated object, EOF, under a single deadline."""
    end = time.monotonic() + deadline_seconds

    def time_left(connection):
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise ReadError("timeout")
        connection.settimeout(remaining)

    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner_uid or info.st_mode & stat.S_IWOTH:
            raise ReadError("identity_unverified")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            time_left(connection)
            connection.connect(str(path))
            if _peer_uid(connection) != owner_uid:
                raise ReadError("identity_unverified")
            time_left(connection)
            connection.sendall(REQUEST)
            connection.shutdown(socket.SHUT_WR)
            reply = bytearray()
            while True:
                time_left(connection)
                chunk = connection.recv(MAX_RESPONSE + 1 - len(reply))
                if not chunk:
                    break
                reply.extend(chunk)
                if len(reply) > MAX_RESPONSE:
                    raise ReadError("response_too_large")
    except socket.timeout as error:
        raise ReadError("timeout") from error
    except OSError as error:
        raise ReadError("endpoint_unavailable") from error
    if not reply.endswith(b"\n") or reply.count(b"\n") != 1:
        raise ReadError("invalid_response")
    try:
        result = parse_json(reply)
    except (ValueError, RecursionError) as error:
        raise ReadError("invalid_response") from error
    if time.monotonic() >= end:
        raise ReadError("timeout")
    return result


def normalize(reply, received, previous):
    """Validate the owner attestation and reduce the response to two primitives."""
    if (isinstance(reply, dict) and set(reply) == {"schema_version", "error"}
            and type(reply["schema_version"]) is int and reply["schema_version"] == 1
            and isinstance(reply["error"], str) and reply["error"] in READ_FAILURES):
        raise ReadError(reply["error"])
    if (not isinstance(reply, dict) or set(reply) != REPLY_FIELDS
            or type(reply["schema_version"]) is not int or reply["schema_version"] != 1
            or reply["operation"] != "get_network_status"):
        raise ReadError("invalid_response")
    if reply["identity"] != "verified":
        raise ReadError("identity_unverified")
    try:
        observed = epoch(reply["timestamp"])
        uptime = reply["uptime"]
        if type(uptime) not in (int, float) or not 0 <= uptime <= 10_000_000_000 or not math.isfinite(uptime):
            raise ValueError("Bad uptime")
    except (ValueError, OverflowError) as error:
        raise ReadError("invalid_response") from error
    if observed > received:
        raise ReadError("future")
    if received - observed >= FRESH_SECONDS:
        raise ReadError("stale")
    if previous is not None and observed <= previous:
        raise ReadError("replayed")
    return observed, float(uptime)
