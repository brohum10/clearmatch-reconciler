from __future__ import annotations

import itertools
import json
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from clearmatch import DataError, Record, ReconcileConfig, read_records, reconcile
from clearmatch.matching import Candidate, _min_cost_maximum_matching
from clearmatch.model import fingerprint


ROOT = Path(__file__).resolve().parents[1]


def record(identifier: str, amount: int = 100, *, account: str = "shop",
           currency: str = "USD", booked: str = "2026-09-01", reference: str = "REF-1") -> Record:
    return Record(identifier, account, amount, currency, date.fromisoformat(booked), reference)


class InputTests(unittest.TestCase):
    def test_sample_csvs_load(self):
        self.assertEqual(len(read_records(ROOT / "examples/orders.csv")), 6)
        self.assertEqual(len(read_records(ROOT / "examples/settlements.csv")), 6)

    def test_duplicate_id_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.csv"
            path.write_text("id,account,amount_cents,currency,date,reference\n"
                            "one,shop,100,USD,2026-09-01,x\n"
                            "one,shop,100,USD,2026-09-02,x\n")
            with self.assertRaisesRegex(DataError, "duplicate id"):
                read_records(path)

    def test_bad_headers_amount_and_date_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.csv"
            path.write_text("id,account,amount_cents,currency,date\n")
            with self.assertRaisesRegex(DataError, "expected columns"):
                read_records(path)
            path.write_text("id,account,amount_cents,currency,date,reference\n"
                            "one,shop,-1,USD,2026-09-01,x\n")
            with self.assertRaisesRegex(DataError, "positive integer"):
                read_records(path)
            path.write_text("id,account,amount_cents,currency,date,reference\n"
                            "one,shop,100,USD,2026-02-30,x\n")
            with self.assertRaisesRegex(DataError, "invalid calendar date"):
                read_records(path)

    def test_duplicate_header_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.csv"
            path.write_text("id,id,account,amount_cents,currency,date,reference\n"
                            "one,one,shop,100,USD,2026-09-01,x\n")
            with self.assertRaisesRegex(DataError, "duplicate CSV header"):
                read_records(path)

    def test_canonical_fingerprint_ignores_row_order(self):
        rows = [record("b"), record("a")]
        self.assertEqual(fingerprint(rows), fingerprint(list(reversed(rows))))


class MatchingTests(unittest.TestCase):
    def test_sample_reconciliation_and_conservation(self):
        orders = read_records(ROOT / "examples/orders.csv")
        settlements = read_records(ROOT / "examples/settlements.csv")
        report = reconcile(orders, settlements)
        self.assertEqual(report["counts"]["automatic_matches"], 3)
        self.assertEqual(report["counts"]["review_suggestions"], 2)
        self.assertEqual(report["unpaired_orders"], ["O-105"])
        self.assertEqual(report["unpaired_settlements"], ["S-999"])
        self.assertEqual(report["amounts_cents"]["automatically_matched_orders"], 27_000)
        self.assertEqual(report["amounts_cents"]["automatically_matched_settlements"], 27_000)
        all_pairs = report["automatic_matches"] + report["review_suggestions"]
        self.assertEqual(len({item["order_id"] for item in all_pairs}), len(all_pairs))
        self.assertEqual(len({item["settlement_id"] for item in all_pairs}), len(all_pairs))

    def test_permuting_input_does_not_change_report(self):
        orders = read_records(ROOT / "examples/orders.csv")
        settlements = read_records(ROOT / "examples/settlements.csv")
        self.assertEqual(reconcile(orders, settlements),
                         reconcile(list(reversed(orders)), list(reversed(settlements))))

    def test_account_and_currency_are_hard_boundaries(self):
        orders = [record("o1", account="a"), record("o2", currency="EUR")]
        settlements = [record("s1", account="b"), record("s2", currency="GBP")]
        report = reconcile(orders, settlements)
        self.assertEqual(report["counts"]["candidates"], 0)
        self.assertEqual(report["unpaired_orders"], ["o1", "o2"])

    def test_date_window_and_amount_tolerance(self):
        order = record("o", amount=100)
        settlement = record("s", amount=101, booked="2026-09-03")
        self.assertEqual(reconcile([order], [settlement])["counts"]["candidates"], 0)
        report = reconcile([order], [settlement], ReconcileConfig(2, 1))
        self.assertEqual(report["counts"]["automatic_matches"], 1)
        self.assertEqual(report["automatic_matches"][0]["amount_delta_cents"], 1)
        self.assertEqual(report["amounts_cents"]["automatically_matched_orders"], 100)
        self.assertEqual(report["amounts_cents"]["automatically_matched_settlements"], 101)
        self.assertEqual(reconcile([order], [record("late", booked="2026-09-05")])
                         ["counts"]["candidates"], 0)

    def test_partial_reference_can_auto_match_but_missing_reference_cannot(self):
        order = record("o", reference="ORDER-1042")
        partial = record("s", reference="1042")
        self.assertEqual(reconcile([order], [partial])["counts"]["automatic_matches"], 1)
        missing = record("x", reference="")
        report = reconcile([order], [missing])
        self.assertEqual(report["counts"]["automatic_matches"], 0)
        self.assertEqual(report["review_suggestions"][0]["reasons"], ["low_confidence"])

    def test_ambiguous_pair_requires_review(self):
        orders = [record("o1"), record("o2")]
        settlements = [record("s1")]
        report = reconcile(orders, settlements)
        self.assertEqual(report["counts"]["automatic_matches"], 0)
        self.assertEqual(report["counts"]["review_suggestions"], 1)
        self.assertIn("competing_candidate", report["review_suggestions"][0]["reasons"])

    def test_tied_scores_have_stable_pairing(self):
        orders = [record("o2"), record("o1")]
        settlements = [record("s2"), record("s1")]
        first = reconcile(orders, settlements)
        second = reconcile(list(reversed(orders)), list(reversed(settlements)))
        self.assertEqual(first, second)
        self.assertEqual(first["counts"]["review_suggestions"], 2)

    def test_global_matching_avoids_greedy_cardinality_trap(self):
        orders = [record("o1", amount=100), record("o2", amount=101)]
        settlements = [record("s1", amount=100), record("s2", amount=99)]
        report = reconcile(orders, settlements, ReconcileConfig(amount_tolerance_cents=1))
        pairs = report["automatic_matches"] + report["review_suggestions"]
        self.assertEqual({(item["order_id"], item["settlement_id"]) for item in pairs},
                         {("o1", "s2"), ("o2", "s1")})

    def test_invalid_config_and_direct_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            ReconcileConfig(date_window_days=-1)
        with self.assertRaisesRegex(DataError, "duplicate order"):
            reconcile([record("o"), record("o")], [])

    def test_empty_ledgers_produce_empty_report(self):
        report = reconcile([], [])
        self.assertEqual(report["counts"]["candidates"], 0)
        self.assertEqual(report["automatic_matches"], [])

    def test_flow_matches_brute_force_optimum_on_small_graphs(self):
        rng = random.Random(7)
        for _ in range(40):
            candidates = [Candidate(f"o{i}", f"s{j}", rng.randrange(12), 0, 0, "exact")
                          for i, j in itertools.product(range(4), repeat=2)
                          if rng.random() < 0.55]
            chosen = _min_cost_maximum_matching(candidates)
            cost_lookup = {(item.order_id, item.settlement_id): item.cost for item in candidates}
            optimum = (0, 0)

            def search(order_index: int, used: set[str], count: int, cost: int) -> None:
                nonlocal optimum
                if order_index == 4:
                    optimum = max(optimum, (count, -cost))
                    return
                search(order_index + 1, used, count, cost)
                for settlement_index in range(4):
                    key = (f"o{order_index}", f"s{settlement_index}")
                    if key in cost_lookup and key[1] not in used:
                        search(order_index + 1, used | {key[1]}, count + 1,
                               cost + cost_lookup[key])

            search(0, set(), 0, 0)
            self.assertEqual((len(chosen), -sum(item.cost for item in chosen)), optimum)


class CommandLineTests(unittest.TestCase):
    def test_cli_writes_deterministic_json_to_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "report.json"
            command = [sys.executable, "-m", "clearmatch", "examples/orders.csv",
                       "examples/settlements.csv", "--output", str(target)]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            first = target.read_text()
            self.assertEqual(json.loads(first)["counts"]["automatic_matches"], 3)
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(), first)

    def test_cli_invalid_input_exits_two(self):
        result = subprocess.run([sys.executable, "-m", "clearmatch", "missing.csv",
                                 "examples/settlements.csv"], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read", result.stderr)


if __name__ == "__main__":
    unittest.main()
