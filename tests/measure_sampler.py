"""Measure capped history and query usage with fictional time on the executing Mac."""
from collections import deque
from contextlib import closing
import json
from pathlib import Path
import resource
import sqlite3
import sys
import tempfile
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sampler.firewalla_health import sampler, storage, transport  # noqa: E402


def measure(directory):
    start = 1_800_000_000 - 1_800_000_000 % 86_400
    reads = 0
    previous = deque(maxlen=4)
    cpu_peak = 0
    storage_peak = 0
    for index in range(31 * 288):
        now = start + index * 300

        def read():
            nonlocal reads
            reads += 1
            data = json.dumps({
                "schema_version": 1, "operation": "get_network_status",
                "identity": "verified", "timestamp": transport.utc(now - 1), "uptime": 10000 - index,
            }).encode()
            return transport.parse_json(data.ljust(16384))

        begin = time.process_time()
        result = sampler.sample(directory, read, clock=lambda: now, require_heap_limit=True)
        assert result == "observed", result
        previous.append(time.process_time() - begin)
        cpu_peak = max(cpu_peak, sum(previous))
        storage_peak = max(storage_peak, sum(p.stat().st_size for p in directory.iterdir()))
    assert reads == 31 * 288
    with closing(sqlite3.connect(directory / storage.DB_NAME)) as db:
        samples = db.execute("SELECT count(*) FROM samples").fetchone()[0]
        incidents = db.execute("SELECT count(*) FROM incidents").fetchone()[0]
        assert samples == 2016 and incidents == 3000, (samples, incidents)
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
    snapshot = sampler.status(directory, now=now)
    assert snapshot["reads_reserved_today"] == 288
    assert snapshot["incidents_today"] == 100 and snapshot["overflow_today"] == 188
    print(json.dumps({
        "simulated_days": 31, "fixture_reads": reads, "reads_on_last_day": 288,
        "health_rows_after_prune": samples, "incident_rows_after_prune": incidents,
        "overflow_on_last_day": 188, "peak_committed_state_bytes": storage_peak,
        "max_four_sample_cpu_ms": round(cpu_peak * 1000, 2),
        "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "sqlite_integrity": "ok",
    }, indent=2))


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("Run this measurement on macOS")
    with tempfile.TemporaryDirectory(prefix="firewalla-resource-") as temporary:
        measure(Path(temporary))
