#!/usr/bin/env python3
"""Capture independently verified Firewalla identity in the credential owner's terminal."""
import ctypes
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import tempfile
import warnings


STATE = Path("/var/db/firewalla-health-provider")
TARGET = Path("/var/db/hermes-homelab/secrets/firewalla/target-host")


def protected_target():
    fd = os.open(TARGET, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError
        data = source.read(65)
    if len(data) > 64:
        raise ValueError
    return data.decode("ascii").strip()


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _publish(record, facts):
    fd, name = tempfile.mkstemp(prefix=".enrollment-", dir=record.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as output:
            json.dump(facts, output, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        rename_exclusive = ctypes.CDLL(None, use_errno=True).renamex_np
        rename_exclusive.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_exclusive.restype = ctypes.c_int
        # Darwin RENAME_EXCL publishes the synced file without replacing a pin.
        if rename_exclusive(os.fsencode(temporary), os.fsencode(record), 0x4) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        directory = os.open(record.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def enroll(sampler, *, hidden=getpass.getpass):
    """Create once. Inputs are local and are never printed, returned or logged."""
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError
    record = STATE / "identity.json"
    if os.path.lexists(record):
        raise FileExistsError
    if sampler <= 0 or sampler == os.getuid() or pwd.getpwuid(sampler).pw_uid != sampler:
        raise ValueError
    target = protected_target()
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        group = hidden("Independently verified Firewalla paired-group reference (hidden): ").strip()
        address = hidden("Independently verified physical appliance LAN IPv4 address (hidden): ").strip()
    ranges = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    parsed = ipaddress.IPv4Address(address)
    if (address != str(parsed) or target != address
            or not any(parsed in ipaddress.IPv4Network(network) for network in ranges)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", group, re.ASCII) is None):
        raise ValueError
    facts = {"schema_version": 1, "samplerUid": sampler, "groupDigest": digest(group), "targetDigest": digest(address)}
    _publish(record, facts)


def main(argv):
    try:
        if (len(argv) != 1 or os.getuid() != pwd.getpwnam("hermes").pw_uid or os.getuid() == 0
                or not sys.stdin.isatty() or not sys.stderr.isatty()):
            raise ValueError
        os.umask(0o077)
        with open("/dev/tty", "rb", buffering=0) as terminal:
            if not os.isatty(terminal.fileno()):
                raise ValueError
            enroll(int(argv[0]))
    except (OSError, ValueError, EOFError, KeyError, getpass.GetPassWarning, KeyboardInterrupt):
        print("Identity setup unavailable or declined. No pairing or network operation was requested.")
        return 1
    print("Owner pin saved. Authenticated paired-box verification remains required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
