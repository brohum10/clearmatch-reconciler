"""Read two CSV ledgers and write a deterministic audit report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .matching import ReconcileConfig, reconcile
from .model import DataError, read_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clearmatch",
                                     description="Reconcile synthetic or exported transaction CSVs")
    parser.add_argument("orders", type=Path)
    parser.add_argument("settlements", type=Path)
    parser.add_argument("--date-window", type=int, default=3, metavar="DAYS")
    parser.add_argument("--amount-tolerance", type=int, default=0, metavar="CENTS")
    parser.add_argument("--max-auto-cost", type=int, default=50)
    parser.add_argument("--min-margin", type=int, default=15)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output", type=Path, help="write report atomically to this file")
    args = parser.parse_args(argv)
    try:
        config = ReconcileConfig(args.date_window, args.amount_tolerance,
                                 args.max_auto_cost, args.min_margin)
        report = reconcile(read_records(args.orders), read_records(args.settlements), config)
        if args.format == "json":
            rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        else:
            counts = report["counts"]
            rendered = (f"{counts['automatic_matches']} automatic matches · "
                        f"{counts['review_suggestions']} review suggestions · "
                        f"{len(report['unpaired_orders'])} unpaired orders · "
                        f"{len(report['unpaired_settlements'])} unpaired settlements\n")
            for item in report["review_suggestions"]:
                rendered += (f"REVIEW {item['order_id']} ↔ {item['settlement_id']}: "
                             f"{', '.join(item['reasons'])}\n")
        if args.output is None:
            print(rendered, end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                                 dir=args.output.parent, prefix=".clearmatch-",
                                                 delete=False) as temporary:
                    temporary.write(rendered)
                    temporary_path = Path(temporary.name)
                os.replace(temporary_path, args.output)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
    except (DataError, ValueError, OSError) as exc:
        print(f"clearmatch: {exc}", file=sys.stderr)
        return 2
    return 0
