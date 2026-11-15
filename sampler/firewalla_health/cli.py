"""LaunchAgent entry points. Production has one state path and one operation."""
import argparse
import json
from pathlib import Path
import pwd
import sys
import time

from . import limits, observer, sampler, transport


def state_path():
    return Path.home() / "Library" / "Application Support" / "Firewalla Health Sampler" / "state"


def run():
    if sys.platform != "darwin":
        return 2
    try:
        owner = pwd.getpwnam(transport.OWNER_ACCOUNT).pw_uid
        limits.apply()
    except (KeyError, OSError, ValueError):
        return 2
    if not limits.within_memory():
        return 2

    def read():
        if not limits.within_memory():
            raise transport.ReadError("resource_limit")
        reply = transport.local_status(transport.SOCKET_PATH, owner)
        if not limits.within_memory():
            raise transport.ReadError("resource_limit")
        return reply

    result = sampler.sample(state_path(), read, settle=time.sleep, require_heap_limit=True)
    if not limits.within_memory():
        return 2
    if result in ("observed", "already_sampled", "too_soon", "busy", "read_limit"):
        return 0
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "status"))
    args = parser.parse_args(argv)
    if args.action == "run":
        return run()
    now = time.time()
    output = sampler.status(state_path(), now=now)
    output["observer_context"] = observer.current(now)
    print(json.dumps(output, separators=(",", ":")))
    return 0
