"""Offline algorithms over already-minimized fictional snapshots."""
import re


MAX_ITEMS = 256
REFERENCE = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,47}", re.ASCII)
KINDS = {
    "alarms": ("high_severity_alarm", 1200),
    "devices": ("newly_observed_device", 1800),
    "rules": ("firewall_policy_drift", 7200),
    "vlans": ("vlan_drift", 7200),
}
ITEM_FIELDS = {
    "alarms": {"ref", "severity", "event_at"},
    "devices": {"ref"},
    "rules": {"ref", "action", "enabled", "priority", "policy"},
    "vlans": {"ref", "tag", "isolated", "segment"},
}


class CoverageError(Exception):
    """The fixture cannot support a comparison."""


def _ref(value):
    return isinstance(value, str) and REFERENCE.fullmatch(value) is not None


def _value(source, item, observed_at):
    if source == "devices":
        return True
    if source == "alarms":
        severity, event = item["severity"], item["event_at"]
        if severity not in ("critical", "high", "medium", "low") or type(event) is not int or not 0 <= event <= observed_at:
            raise CoverageError("invalid_snapshot")
        return severity, event
    if source == "rules":
        action, enabled, priority, policy = (item[k] for k in ("action", "enabled", "priority", "policy"))
        if (action not in ("allow", "block", "route", "qos", "match") or type(enabled) is not bool
                or type(priority) is not int or not 0 <= priority <= 65_535 or not _ref(policy)):
            raise CoverageError("invalid_snapshot")
        return action, enabled, priority, policy
    tag, isolated, segment = (item[k] for k in ("tag", "isolated", "segment"))
    if type(tag) is not int or not 1 <= tag <= 4094 or type(isolated) is not bool or not _ref(segment):
        raise CoverageError("invalid_snapshot")
    return tag, isolated, segment


def _snapshot(source, data, now):
    if not isinstance(data, dict) or set(data) != {"observed_at", "complete", "items"}:
        raise CoverageError("invalid_snapshot")
    at = data["observed_at"]
    if type(at) is not int or not 0 <= at <= now:
        raise CoverageError("invalid_snapshot")
    if now - at >= KINDS[source][1]:
        raise CoverageError("stale_snapshot")
    if data["complete"] is not True:
        raise CoverageError("incomplete_snapshot")
    if not isinstance(data["items"], list) or len(data["items"]) > MAX_ITEMS:
        raise CoverageError("incomplete_snapshot")
    items = {}
    for item in data["items"]:
        if (not isinstance(item, dict) or set(item) != ITEM_FIELDS[source]
                or not _ref(item["ref"]) or item["ref"] in items):
            raise CoverageError("invalid_snapshot")
        items[item["ref"]] = _value(source, item, at)
    return at, items


def _count(source, previous, old, new):
    if source == "alarms":
        if any(old[ref][1] != new[ref][1] for ref in old.keys() & new.keys()):
            raise CoverageError("incomparable_snapshots")
        # The fixture windows must overlap. IDs suppress repeats within these two views.
        return sum(
            ref not in old and severity in ("high", "critical") and event > previous
            for ref, (severity, event) in new.items()
        )
    if source == "devices":
        return len(new.keys() - old.keys())
    return sum(old.get(ref) != new.get(ref) for ref in old.keys() | new.keys())


def evaluate(source, previous, current, now):
    if not isinstance(source, str) or source not in KINDS:
        return {"state": "unknown", "reason": "unsupported_source"}
    try:
        current_at, new = _snapshot(source, current, now)
        if previous is None:
            raise CoverageError("baseline_required")
        previous_at, old = _snapshot(source, previous, now)
        if previous_at >= current_at:
            raise CoverageError("incomparable_snapshots")
        count = _count(source, previous_at, old, new)
        return {"state": "observed", "kind": KINDS[source][0], "count": count}
    except CoverageError as error:
        return {"state": "unknown", "reason": str(error)}
