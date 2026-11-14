"""Reserve one slot, collect health and atomically update normalized evidence."""
from contextlib import closing
import sqlite3
import time

from . import storage, transport


INTERVAL = 300
MAX_SCHEDULE_JITTER = 10
DAY = 86_400
MAX_READS = 288
MAX_INCIDENTS = 100
SAMPLE_AGE = 7 * DAY
INCIDENT_AGE = 30 * DAY
INCIDENT_KINDS = {"telemetry_gap", "possible_process_restart"}


def _progress(db):
    return db.execute("SELECT * FROM progress WHERE id=1").fetchone()


def _incident(db, kind, now):
    day = int(now) // DAY
    db.execute("INSERT OR IGNORE INTO incident_quota VALUES (?,0,0)", (day,))
    changed = db.execute(
        "UPDATE incident_quota SET recorded=recorded+1 WHERE day=? AND recorded<?",
        (day, MAX_INCIDENTS),
    )
    if not changed.rowcount:
        db.execute("UPDATE incident_quota SET overflow=overflow+1 WHERE day=?", (day,))
        return None
    return db.execute("INSERT INTO incidents (kind,opened) VALUES (?,?)", (kind, int(now))).lastrowid


def _miss(db, progress, through_slot, intervals, now):
    missed = min(2, progress["missed"] + intervals)
    active, gap_id = progress["gap_active"], progress["gap_id"]
    if missed == 2 and not active:
        active = True
        gap_id = _incident(db, "telemetry_gap", now)
    db.execute(
        "UPDATE progress SET last_slot=?,missed=?,fresh=0,gap_active=?,gap_id=? WHERE id=1",
        (through_slot, missed, active, gap_id),
    )


def _prune(db, now):
    db.execute("DELETE FROM samples WHERE started<=?", (now - SAMPLE_AGE,))
    db.execute("DELETE FROM incidents WHERE opened<=?", (now - INCIDENT_AGE,))
    db.execute("DELETE FROM incident_quota WHERE day<?", ((int(now) - INCIDENT_AGE) // DAY,))
    db.execute(
        "UPDATE progress SET last_observed=NULL,last_uptime=NULL WHERE last_observed<=?",
        (now - SAMPLE_AGE,),
    )


def _reserve(db, now):
    slot = int(now) // INTERVAL
    db.execute("BEGIN IMMEDIATE")
    last = db.execute("SELECT slot,started FROM samples ORDER BY slot DESC LIMIT 1").fetchone()
    if last is not None:
        if now < last["started"]:
            return "clock_rollback"
        if slot == last["slot"]:
            return "already_sampled"
        if now - last["started"] < INTERVAL:
            return "too_soon"
    count = db.execute("SELECT count(*) FROM samples WHERE started>?", (now - DAY,)).fetchone()[0]
    if count >= MAX_READS:
        return "read_limit"
    db.execute(
        "INSERT OR IGNORE INTO progress VALUES(1,?,0,0,0,NULL,NULL,NULL)", (slot - 1,),
    )
    _prune(db, now)
    current = _progress(db)
    missing = slot - current["last_slot"] - 1
    if missing > 0:
        db.execute("UPDATE samples SET state='unknown',reason='interrupted' WHERE state='pending'")
        _miss(db, current, slot - 1, missing, now)
    db.execute("INSERT INTO samples (slot,started,state) VALUES (?,?,'pending')", (slot, now))
    return None


def _complete(db, started, ended, observation, reason):
    slot = int(started) // INTERVAL
    current = _progress(db)
    db.execute("BEGIN IMMEDIATE")
    if observation is None:
        db.execute(
            "UPDATE samples SET received=?,state='unknown',reason=? WHERE slot=?", (ended, reason, slot),
        )
        _miss(db, current, slot, 1, started)
        return "unknown"
    observed, uptime = observation
    db.execute(
        "UPDATE samples SET received=?,state='observed',observed=?,uptime=? WHERE slot=?",
        (ended, observed, uptime, slot),
    )
    fresh = min(2, current["fresh"] + 1)
    active, gap_id = current["gap_active"], current["gap_id"]
    if active and fresh == 2:
        if gap_id is not None:
            db.execute("UPDATE incidents SET recovered=? WHERE id=?", (started, gap_id))
        active, gap_id = False, None
    if current["last_uptime"] is not None and uptime < current["last_uptime"]:
        _incident(db, "possible_process_restart", started)
    db.execute(
        "UPDATE progress SET last_slot=?,missed=0,fresh=?,gap_active=?,gap_id=?,"
        "last_observed=?,last_uptime=? WHERE id=1",
        (slot, fresh, active, gap_id, observed, uptime),
    )
    return "observed"


def sample(directory, read, *, clock=time.time, open_db=None, settle=None, require_heap_limit=False):
    """Take one real or fixture reading. Commit the attempt before calling read()."""
    def connect(path):
        return storage.connect(path, require_heap_limit=require_heap_limit)

    connect_state = open_db or connect
    try:
        storage.state_directory(directory, create=True)
        with storage.exclusive(directory), closing(connect_state(directory)) as db:
            started = clock()
            with db:
                skipped = _reserve(db, started)
            if skipped == "too_soon" and settle is not None:
                last = db.execute("SELECT started FROM samples ORDER BY slot DESC LIMIT 1").fetchone()[0]
                remaining = last + INTERVAL - started
                if 0 < remaining <= MAX_SCHEDULE_JITTER:
                    settle(remaining)
                    started = clock()
                    with db:
                        skipped = _reserve(db, started)
            if skipped:
                return skipped
            previous = _progress(db)["last_observed"]
            observation, reason = None, None
            try:
                reply = read()
                ended = clock()
                if ended < started:
                    raise transport.ReadError("clock_rollback")
                observation = transport.normalize(reply, ended, previous)
            except transport.ReadError as error:
                reason = error.reason if error.reason in transport.READ_FAILURES else "invalid_response"
                ended = clock()
            with db:
                return _complete(db, started, int(max(started, ended)), observation, reason)
    except storage.Busy:
        return "busy"
    except (OSError, sqlite3.Error, storage.UnsafeState) as error:
        return storage.storage_reason(error)


def _health(last, now):
    if last is None:
        return {"state": "unknown", "reason": "no_samples"}
    if now < last["started"]:
        return {"state": "unknown", "reason": "clock_rollback"}
    if last["state"] == "pending":
        return {"state": "unknown", "reason": "interrupted"}
    if last["state"] == "unknown":
        if last["reason"] not in transport.READ_FAILURES | {"interrupted"}:
            raise storage.UnsafeState
        return {"state": "unknown", "reason": last["reason"]}
    if last["state"] != "observed":
        raise storage.UnsafeState
    if last["observed"] > now or now - last["observed"] >= transport.FRESH_SECONDS:
        return {"state": "unknown", "reason": "stale"}
    return {
        "state": "observed", "observed_at": transport.utc(last["observed"]),
        "received_at": transport.utc(last["received"]), "reported_uptime_s": int(last["uptime"]),
    }


def status(directory, *, now=None):
    now = time.time() if now is None else now
    result = {"schema_version": 1, "source": "firewalla_status",
              "gap_active": None, "incidents": []}
    try:
        storage.state_directory(directory, create=False)
        with closing(storage.connect(directory, readonly=True)) as db:
            # A read transaction keeps the status consistent with the incident/attempt counts.
            db.execute("BEGIN")
            last = db.execute("SELECT * FROM samples ORDER BY slot DESC LIMIT 1").fetchone()
            progress = _progress(db)
            quota = db.execute("SELECT * FROM incident_quota WHERE day=?", (int(now) // DAY,)).fetchone()
            window = (now - INCIDENT_AGE, now)
            incidents = db.execute(
                "SELECT * FROM incidents WHERE opened>? AND opened<=? ORDER BY id DESC LIMIT 100",
                window,
            ).fetchall()
            if any(row["kind"] not in INCIDENT_KINDS for row in incidents):
                raise storage.UnsafeState
            total_incidents = db.execute(
                "SELECT count(*) FROM incidents WHERE opened>? AND opened<=?", window,
            ).fetchone()[0]
            gap_persisted = bool(progress["gap_active"]) if progress else False
            ended_without_attempt = max(0, int(now) // INTERVAL - progress["last_slot"] - 1) if progress else 0
            gap_due = progress is not None and progress["missed"] + ended_without_attempt >= 2
            result.update({
                "health": _health(last, now),
                "gap_active": gap_persisted or gap_due,
                "gap_persisted": gap_persisted,
                "reads_reserved_today": db.execute(
                    "SELECT count(*) FROM samples WHERE started>=? AND started<=?",
                    (int(now) // DAY * DAY, now),
                ).fetchone()[0],
                "incidents_today": quota["recorded"] if quota else 0,
                "overflow_today": quota["overflow"] if quota else 0,
                "incidents_omitted": total_incidents - len(incidents),
                "last_attempt_at": transport.utc(last["started"]) if last else None,
                "last_success_at": (
                    transport.utc(progress["last_observed"])
                    if progress and progress["last_observed"] is not None else None
                ),
                "next_read_eligible_at": transport.utc(last["started"] + INTERVAL) if last else None,
                "incidents": [{
                    "kind": row["kind"], "opened_at": transport.utc(row["opened"]),
                    "recovered_at": transport.utc(row["recovered"]) if row["recovered"] is not None else None,
                } for row in incidents],
            })
    except FileNotFoundError:
        result["health"] = {"state": "unknown", "reason": "no_samples"}
    except (OSError, sqlite3.Error, storage.UnsafeState, TypeError, ValueError, OverflowError) as error:
        result["health"] = {"state": "unknown", "reason": storage.storage_reason(error)}
    return result
