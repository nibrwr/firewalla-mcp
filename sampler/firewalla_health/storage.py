"""Private SQLite state and exclusive access for one-off launchd invocations."""
from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import struct

DB_NAME = "health.sqlite3"
LOCK_NAME = "sample.lock"
JOURNAL_NAME = DB_NAME + "-journal"
FILE_CAP = 12 * 1024 * 1024
SQLITE_PAGES = 2048
PAGE_SIZE = 4096
# Darwin's struct flock uses two off_t fields, pid_t and two short fields.
LOCK_RANGE = struct.Struct("@qqihh")

SCHEMA = (
    """CREATE TABLE samples (
        slot INTEGER PRIMARY KEY, started REAL NOT NULL, received INTEGER,
        state TEXT NOT NULL, reason TEXT, observed REAL, uptime REAL
    )""",
    """CREATE TABLE incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, opened INTEGER NOT NULL,
        recovered INTEGER
    )""",
    """CREATE TABLE incident_quota (
        day INTEGER PRIMARY KEY, recorded INTEGER NOT NULL, overflow INTEGER NOT NULL
    )""",
    """CREATE TABLE progress (
        id INTEGER PRIMARY KEY CHECK (id = 1), last_slot INTEGER NOT NULL,
        missed INTEGER NOT NULL, fresh INTEGER NOT NULL, gap_active INTEGER NOT NULL,
        gap_id INTEGER, last_observed REAL, last_uptime REAL
    )""",
)


class UnsafeState(Exception):
    pass


class Busy(Exception):
    pass


def _private_file(info, limit):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()
            or info.st_size > limit):
        raise UnsafeState
    return info


def private_file(path, *, limit=FILE_CAP):
    return _private_file(path.lstat(), limit)


def _lock_range(fd, offset, access):
    request = LOCK_RANGE.pack(offset, 1, 0, access, os.SEEK_SET)
    fcntl.fcntl(fd, fcntl.F_OFD_SETLK, request)


def track_reservation(fd, slot):
    # Byte zero excludes other writers. A distinct range belongs to this attempt.
    _lock_range(fd, slot + 1, fcntl.F_WRLCK)


def writer_active(directory, slot):
    """Check the named reservation without creating or changing state."""
    try:
        fd = os.open(directory / LOCK_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        _private_file(os.fstat(fd), 0)
        try:
            _lock_range(fd, slot + 1, fcntl.F_RDLCK)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


def state_directory(directory, *, create):
    if create:
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        raise UnsafeState
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in (DB_NAME, LOCK_NAME, JOURNAL_NAME):
                raise UnsafeState
            try:
                private_file(Path(entry.path))
            except FileNotFoundError:
                # A reader may inspect the directory while SQLite removes its journal.
                if entry.name != JOURNAL_NAME:
                    raise


@contextmanager
def exclusive(directory):
    path = directory / LOCK_NAME
    fd = os.open(path, os.O_NOFOLLOW | os.O_CREAT | os.O_NONBLOCK | os.O_RDWR, 0o600)
    try:
        _private_file(os.fstat(fd), 0)
        try:
            _lock_range(fd, 0, fcntl.F_WRLCK)
        except BlockingIOError as error:
            raise Busy from error
        yield fd
    finally:
        os.close(fd)


def connect(directory, *, readonly=False, require_heap_limit=False):
    path = directory / DB_NAME
    if not readonly:
        try:
            descriptor = os.open(path, os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    private_file(path)
    mode = "ro" if readonly else "rw"
    db = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if readonly:
            if version != 1:
                raise UnsafeState
            return db
        if version not in (0, 1):
            raise UnsafeState
        if version == 0 and db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            raise UnsafeState
        if (db.execute("PRAGMA page_size").fetchone()[0] != PAGE_SIZE
                or db.execute("PRAGMA page_count").fetchone()[0] > SQLITE_PAGES
                or db.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
            raise UnsafeState
        db.execute("PRAGMA max_page_count=2048")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA cache_size=-1024")
        db.execute("PRAGMA cache_spill=OFF")
        heap_limit = db.execute("PRAGMA hard_heap_limit=16777216").fetchone()
        if require_heap_limit and (not heap_limit or not 0 < heap_limit[0] <= 16 * 1024 * 1024):
            raise UnsafeState
        if version == 0:
            db.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                db.execute(statement)
            db.execute("PRAGMA user_version=1")
            db.commit()
        return db
    except (sqlite3.Error, UnsafeState):
        db.close()
        raise


def storage_reason(error):
    if isinstance(error, UnsafeState):
        return "unsafe_state"
    if (getattr(error, "errno", None) in (errno.ENOSPC, errno.EDQUOT, errno.EFBIG)
            or getattr(error, "sqlite_errorcode", None) == getattr(sqlite3, 'SQLITE_FULL', 13)
            or str(error) == "database or disk is full"):
        return "storage_full"
    return "storage_unavailable"
