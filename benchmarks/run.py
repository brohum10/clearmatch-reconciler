"""Seeded, synthetic reconciliation benchmark; no external data."""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clearmatch import Record, reconcile  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=1_000)
    parser.add_argument("--groups", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if min(args.records, args.groups, args.repeats) <= 0:
        parser.error("all arguments must be positive")
    booked = date(2026, 9, 1)
    orders = [Record(f"O-{index:06d}", f"merchant-{index % args.groups:04d}",
                     10_000, "USD", booked, f"ORDER-{index:06d}")
              for index in range(args.records)]
    settlements = [Record(f"S-{index:06d}", f"merchant-{index % args.groups:04d}",
                          10_000, "USD", booked, f"ORDER-{index:06d}")
                   for index in range(args.records)]
    rng = random.Random(42)
    rng.shuffle(orders)
    rng.shuffle(settlements)
    reconcile(orders, settlements)  # Warm-up outside timed trials.
    timings = []
    for _ in range(args.repeats):
        started = time.perf_counter()
        report = reconcile(orders, settlements)
        timings.append((time.perf_counter() - started) * 1_000)
        if report["counts"]["automatic_matches"] != args.records:
            raise RuntimeError("benchmark did not match all synthetic records")
    print(json.dumps({
        "orders": args.records,
        "settlements": args.records,
        "account_groups": args.groups,
        "candidate_edges": report["counts"]["candidates"],
        "automatic_matches": report["counts"]["automatic_matches"],
        "repeats": args.repeats,
        "median_ms": round(statistics.median(timings), 3),
        "min_ms": round(min(timings), 3),
        "max_ms": round(max(timings), 3),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }, indent=2))


if __name__ == "__main__":
    main()
