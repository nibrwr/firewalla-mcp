"""Read only the already approved cached Observer projection for context."""
from datetime import datetime
import os
from pathlib import Path
import stat

from .transport import parse_json, utc


MAX_CACHE_BYTES = 16 * 1024


def _timestamp(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError
    return moment.timestamp()


def context(path, now):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or info.st_size > MAX_CACHE_BYTES):
                return {"state": "unknown"}
            data = source.read(MAX_CACHE_BYTES + 1)
        if len(data) > MAX_CACHE_BYTES:
            return {"state": "unknown"}
        cache = parse_json(data)
        seen, expiry = _timestamp(cache["collected_at"]), _timestamp(cache["fresh_until"])
        rows = cache["targets"]
        if (type(cache["schema_version"]) is not int or cache["schema_version"] != 1
                or not seen <= now < min(expiry, seen + 600)
                or cache["sample_interval_seconds"] != 300
                or not isinstance(rows, list) or len(rows) != 4):
            return {"state": "unknown"}
        allowed = {"healthy", "degraded", "unavailable", "unknown", "warning", "down"}
        for row in rows:
            if (not isinstance(row, dict) or row.get("status") not in allowed
                    or (row.get("up") is not None and (type(row["up"]) is not int or row["up"] not in (0, 1)))):
                return {"state": "unknown"}
        return {
            "state": "observed", "responding": sum(row.get("up") == 1 for row in rows),
            "qualified": 4, "observed_at": utc(seen),
        }
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return {"state": "unknown"}


def current(now):
    path = Path.home() / "Library" / "Application Support" / "M4 Homelab Observer" / "dashboard-status.json"
    return context(path, now)
