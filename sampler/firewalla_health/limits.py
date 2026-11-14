"""Per-invocation limits for the one-process LaunchAgent."""
import resource
import signal

from .storage import FILE_CAP


MAX_RSS = 100 * 1024 * 1024


def _limit(kind, soft, hard):
    old_soft, old_hard = resource.getrlimit(kind)
    soft = min(soft, old_soft) if old_soft != resource.RLIM_INFINITY else soft
    hard = min(hard, old_hard) if old_hard != resource.RLIM_INFINITY else hard
    resource.setrlimit(kind, (min(soft, hard), hard))


def apply():
    # Each request slot admits one process doing real collection. Four possible
    # boundary invocations at two CPU seconds each fit inside a 15-minute budget.
    _limit(resource.RLIMIT_CPU, 1, 2)
    _limit(resource.RLIMIT_CORE, 0, 0)
    _limit(resource.RLIMIT_FSIZE, FILE_CAP, FILE_CAP)
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)


def within_memory():
    # Darwin reports ru_maxrss in bytes.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss < MAX_RSS
