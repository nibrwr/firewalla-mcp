"""Acceptance scenarios through the sampler and real SQLite, with an isolated reader."""
from contextlib import closing
import multiprocessing
import os
from pathlib import Path
import sqlite3
import resource
import signal
import tempfile
import unittest

from sampler.firewalla_health import sampler, storage, transport


START = 1_800_000_000


def observed(clock, value=1000):
    return {
        "schema_version": 1,
        "operation": "get_network_status",
        "identity": "verified",
        "timestamp": transport.utc(clock - 1),
        "uptime": value,
    }


def abandon_after_reserving(directory, at):
    def stop():
        os._exit(19)
    sampler.sample(Path(directory), stop, clock=lambda: at)


def hold_reservation(directory, at, entered, release):
    def wait_for_other_attempt():
        entered.set()
        if not release.wait(5):
            os._exit(22)
        return observed(at)
    outcome = sampler.sample(Path(directory), wait_for_other_attempt, clock=lambda: at)
    os._exit(0 if outcome == "observed" else 23)


def wait_before_reservation(directory, at, entered, release):
    def settle(seconds):
        nonlocal at
        entered.set()
        if not release.wait(5):
            os._exit(24)
        at += seconds
    outcome = sampler.sample(Path(directory), lambda: observed(at), clock=lambda: at, settle=settle)
    os._exit(0 if outcome == "observed" else 25)


def write_when_full(directory, at):
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

    def unexpected_read():
        os._exit(20)

    result = sampler.sample(Path(directory), unexpected_read, clock=lambda: at)
    os._exit(0 if result in {"storage_full", "storage_unavailable"} else 21)


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.at = START
        self.reads = 0

    def tick(self, reply=None):
        def read():
            self.reads += 1
            if isinstance(reply, Exception):
                raise reply
            return observed(self.at) if reply is None else reply
        return sampler.sample(self.directory, read, clock=lambda: self.at)

    def status(self):
        return sampler.status(self.directory, now=self.at)

    def advance(self, slots=1):
        self.at += 300 * slots

    def test_fresh_status_round_trip_and_no_repeat_in_slot(self):
        self.assertEqual(self.tick(), "observed")
        self.assertEqual(self.tick(), "already_sampled")
        self.assertEqual(self.reads, 1)
        status = self.status()
        self.assertEqual(status["health"]["reported_uptime_s"], 1000)
        self.assertEqual(status["health"]["state"], "observed")
        self.assertEqual(status["reads_reserved_today"], 1)
        self.assertEqual(status["incidents"], [])
        self.assertEqual(status["next_read_eligible_at"], transport.utc(self.at + 300))

    def test_two_misses_open_once_and_two_fresh_samples_recover(self):
        self.tick()
        self.advance()
        self.tick(transport.ReadError("timeout"))
        self.assertFalse(self.status()["gap_active"])
        self.advance()
        self.tick(transport.ReadError("timeout"))
        self.assertTrue(self.status()["gap_active"])
        self.advance()
        self.tick(transport.ReadError("timeout"))
        self.advance()
        self.tick()
        self.assertTrue(self.status()["gap_active"])
        self.advance()
        self.tick()
        result = self.status()
        self.assertFalse(result["gap_active"])
        self.assertEqual([x["kind"] for x in result["incidents"]], ["telemetry_gap"])
        self.assertEqual(result["incidents"][0]["recovered_at"], transport.utc(self.at))

    def test_skipped_intervals_and_process_restart(self):
        self.tick(observed(self.at, 9000))
        self.advance(3)
        self.tick(observed(self.at, 20))
        result = self.status()
        self.assertTrue(result["gap_active"])
        self.assertEqual({x["kind"] for x in result["incidents"]}, {
            "telemetry_gap", "possible_process_restart",
        })
        self.advance()
        self.tick(observed(self.at, 320))
        self.assertFalse(self.status()["gap_active"])

    def test_untrusted_stale_replayed_and_future_data_fail_closed(self):
        first = observed(self.at)
        self.tick(first)
        for bad, why in (
            (lambda: first, "replayed"),
            (lambda: {**observed(self.at), "timestamp": transport.utc(self.at + 1)}, "future"),
            (lambda: observed(self.at - 600), "stale"),
            (lambda: {**observed(self.at), "identity": "unconfirmed"}, "identity_unverified"),
            (lambda: {**observed(self.at), "device": "fixture-private-value"}, "invalid_response"),
        ):
            self.advance()
            self.assertEqual(self.tick(bad()), "unknown")
            status = self.status()
            self.assertEqual(status["health"]["reason"], why)
            self.assertNotIn("fixture-private-value", repr(status))
        self.advance()
        self.tick()
        self.advance()
        self.tick()
        self.assertFalse(self.status()["gap_active"])
        self.at += 601
        self.assertEqual(self.status()["health"]["reason"], "stale")

    def test_process_crash_keeps_reservation_and_repairs_on_next_tick(self):
        ctx = multiprocessing.get_context("fork")
        process = ctx.Process(target=abandon_after_reserving, args=(self.directory, self.at))
        process.start()
        process.join(5)
        self.assertEqual(process.exitcode, 19)
        self.assertEqual(self.status()["health"]["reason"], "interrupted")
        self.assertEqual(self.tick(), "already_sampled")
        self.assertEqual(self.reads, 0)
        self.advance(2)
        self.assertEqual(self.tick(), "observed")
        self.assertTrue(self.status()["gap_active"])
        self.advance()
        self.tick()
        self.assertFalse(self.status()["gap_active"])
        with closing(sqlite3.connect(self.directory / "health.sqlite3")) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))

    def test_clock_and_query_cap(self):
        self.at += 289
        self.tick()
        self.at -= 5
        self.assertEqual(self.tick(), "clock_rollback")
        self.at += 16
        self.assertEqual(self.tick(), "too_soon")
        self.assertEqual(self.reads, 1)
        self.at = START + 589
        self.assertEqual(self.tick(), "observed")
        self.assertEqual(self.reads, 2)

    def test_daily_incident_overflow_and_retention(self):
        self.tick(observed(self.at, 200))
        for _ in range(105):
            self.advance()
            self.tick(observed(self.at, 200 - (self.at - START) // 300))
        result = self.status()
        self.assertEqual(result["reads_reserved_today"], 106)
        self.assertEqual(result["incidents_today"], 100)
        self.assertEqual(result["overflow_today"], 5)
        self.at += 8 * 86400
        self.tick()
        with closing(sqlite3.connect(self.directory / "health.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM samples").fetchone(), (1,))
        self.at += 31 * 86400
        self.tick()
        self.assertEqual(self.status()["incidents_today"], 0)
        self.assertTrue(self.status()["gap_active"])
        self.assertEqual(self.status()["incidents"], [])
        with closing(sqlite3.connect(self.directory / "health.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM incidents").fetchone(), (0,))

    def test_disk_failure_before_reservation_never_reads_and_recovers(self):
        self.tick()
        self.advance()
        def full(_):
            error = sqlite3.OperationalError("database or disk is full")
            error.sqlite_errorcode = 13  # SQLite's stable SQLITE_FULL result code.
            raise error
        self.assertEqual(
            sampler.sample(self.directory, self.fail, clock=lambda: self.at, open_db=full),
            "storage_full",
        )
        self.assertEqual(self.status()["reads_reserved_today"], 1)
        self.assertEqual(self.tick(), "observed")
        self.assertEqual(self.status()["reads_reserved_today"], 2)

    def test_active_writer_excludes_another_invocation(self):
        ctx = multiprocessing.get_context("fork")
        entered, release = ctx.Event(), ctx.Event()
        child = ctx.Process(target=hold_reservation, args=(self.directory, self.at, entered, release))
        child.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.tick(), "busy")
            self.assertEqual(self.reads, 0)
        finally:
            release.set()
            child.join(5)
        self.assertEqual(child.exitcode, 0)
        self.assertEqual(self.status()["health"]["state"], "observed")

    def test_real_sqlite_write_failure_does_not_read_and_reopens(self):
        self.tick()
        self.advance()
        ctx = multiprocessing.get_context("fork")
        process = ctx.Process(target=write_when_full, args=(self.directory, self.at))
        process.start()
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(self.status()["reads_reserved_today"], 1)
        self.assertEqual(self.tick(), "observed")
        self.assertEqual(self.reads, 2)
        with closing(sqlite3.connect(self.directory / storage.DB_NAME)) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone(), ("ok",))

    def test_status_detects_two_unattempted_intervals_read_only(self):
        self.tick()
        self.advance(2)
        self.assertFalse(self.status()["gap_active"])
        self.advance()
        status = self.status()
        self.assertTrue(status["gap_active"])
        self.assertFalse(status["gap_persisted"])
        self.assertEqual(status["incidents"], [])
        self.tick()
        self.assertTrue(self.status()["gap_persisted"])
        self.advance()
        self.tick()
        self.assertFalse(self.status()["gap_active"])

    def test_small_dispatch_jitter_waits_instead_of_skipping_a_tick(self):
        self.at += 8.5
        self.tick()
        self.at += 298
        sleeps = []
        def settle(seconds):
            self.at += seconds
            sleeps.append(seconds)
        self.assertEqual(
            sampler.sample(self.directory, lambda: observed(self.at), clock=lambda: self.at, settle=settle),
            "observed",
        )
        self.assertEqual(sleeps, [2])
        self.assertEqual(self.status()["reads_reserved_today"], 2)

    def test_jitter_waits_even_when_early_arrival_is_in_previous_utc_slot(self):
        self.at = START + 1
        self.tick()
        self.at = START + 299
        waits = []

        def settle(seconds):
            waits.append(seconds)
            self.at += seconds

        self.assertEqual(
            sampler.sample(self.directory, lambda: observed(self.at), clock=lambda: self.at, settle=settle),
            "observed",
        )
        self.assertEqual(waits, [2])
        self.assertEqual(self.status()["reads_reserved_today"], 2)

    def test_pending_read_over_a_slot_boundary_does_not_count_as_a_miss(self):
        self.tick(transport.ReadError("timeout"))
        ctx = multiprocessing.get_context("fork")
        entered, release = ctx.Event(), ctx.Event()
        child = ctx.Process(target=hold_reservation, args=(self.directory, START + 599, entered, release))
        child.start()
        self.at = START + 601
        try:
            self.assertTrue(entered.wait(5))
            report = self.status()
            self.assertEqual(report["health"], {"state": "unknown", "reason": "in_progress"})
            self.assertFalse(report["gap_active"])
        finally:
            release.set()
            child.join(5)
        self.assertEqual(child.exitcode, 0)
        self.assertEqual(self.status()["health"]["state"], "observed")

    def test_next_run_waiting_for_jitter_does_not_claim_an_interrupted_reservation(self):
        self.tick(transport.ReadError("timeout"))
        ctx = multiprocessing.get_context("fork")
        crashed = ctx.Process(target=abandon_after_reserving, args=(self.directory, START + 308))
        crashed.start()
        crashed.join(5)
        self.assertEqual(crashed.exitcode, 19)
        entered, release = ctx.Event(), ctx.Event()
        child = ctx.Process(target=wait_before_reservation, args=(self.directory, START + 600, entered, release))
        child.start()
        self.at = START + 601
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.status()["health"], {"state": "unknown", "reason": "interrupted"})
            self.assertTrue(self.status()["gap_active"])
        finally:
            release.set()
            child.join(5)
        self.assertEqual(child.exitcode, 0)
        self.assertTrue(self.status()["gap_persisted"])

    def test_fractional_uptime_decrease_opens_restart_evidence(self):
        self.tick(observed(self.at, 1000.9))
        self.advance()
        self.tick(observed(self.at, 1000.1))
        self.assertEqual([item["kind"] for item in self.status()["incidents"]], ["possible_process_restart"])

    def test_rolling_read_budget_including_restart_of_each_sqlite_connection(self):
        self.at = START - START % 86_400
        first = self.at
        for index in range(288):
            self.tick()
            if index != 287:
                self.advance()
        self.assertEqual(self.reads, 288)
        self.assertEqual(self.status()["reads_reserved_today"], 288)
        self.at += 299.9
        self.assertEqual(self.tick(), "already_sampled")
        self.assertEqual(self.reads, 288)
        self.at = first + 86_400
        self.assertEqual(self.tick(), "observed")
        self.assertEqual(self.reads, 289)
        self.assertEqual(self.status()["reads_reserved_today"], 1)

    def test_status_treats_unrecognized_stored_labels_as_unsafe(self):
        self.tick()
        self.advance()
        self.tick(transport.ReadError("timeout"))
        with closing(sqlite3.connect(self.directory / storage.DB_NAME)) as db, db:
            db.execute("UPDATE samples SET reason='PRIVATE-UNKNOWN' WHERE state='unknown'")
        self.assertEqual(self.status()["health"], {"state": "unknown", "reason": "unsafe_state"})
        self.advance()
        self.tick(observed(self.at, 20))
        with closing(sqlite3.connect(self.directory / storage.DB_NAME)) as db, db:
            db.execute("UPDATE incidents SET kind='PRIVATE-INCIDENT'")
        result = self.status()
        self.assertEqual(result["health"], {"state": "unknown", "reason": "unsafe_state"})
        self.assertEqual(result["incidents"], [])

    def test_bad_permissions_are_detected_before_reading(self):
        self.directory.chmod(0o755)
        self.assertEqual(self.tick(), "unsafe_state")
        self.assertEqual(self.reads, 0)


if __name__ == "__main__":
    unittest.main()
