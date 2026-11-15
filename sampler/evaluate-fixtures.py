#!/usr/bin/env python3
"""Run the four non-live detectors on an explicitly provided fictional corpus."""
import argparse
import json
from pathlib import Path

from firewalla_health.detections import evaluate
from firewalla_health.transport import parse_json


MAX_FIXTURE_BYTES = 256 * 1024
DEFAULT_FIXTURE_TIME = 1_800_000_000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--at", type=int, default=DEFAULT_FIXTURE_TIME)
    args = parser.parse_args()
    try:
        with args.fixture.open("rb") as source:
            payload = source.read(MAX_FIXTURE_BYTES + 1)
        if len(payload) > MAX_FIXTURE_BYTES:
            raise ValueError
        cases = parse_json(payload)
        if not isinstance(cases, list) or len(cases) > 256:
            raise ValueError
        results = [evaluate(item["source"], item["previous"], item["current"], args.at) for item in cases]
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('{"state":"unknown","reason":"invalid_fixture_file"}')
        return 2
    print(json.dumps(results, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
