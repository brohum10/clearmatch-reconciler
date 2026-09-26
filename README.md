# ClearMatch Reconciler

A small, runnable transaction-reconciliation tool. Given two CSV exports—orders and settlements—it proposes one-to-one matches, separates confident matches from items requiring human review, and writes a deterministic audit report. The included data is entirely synthetic.

This is a backend/data-engineering project built around a real operational problem: an amount alone is rarely enough evidence that two financial records belong together. ClearMatch applies hard account/currency/date/amount boundaries, then uses reference quality and a global matching algorithm so it does not blindly take the first plausible pair.

## Run the demo

Requires Python 3.11 or newer. There are no runtime dependencies or API keys. From the repository root:

```bash
python3 -m clearmatch examples/orders.csv examples/settlements.csv --format text
python3 -m clearmatch examples/orders.csv examples/settlements.csv --output reports/sample.json
```

The sample produces **3 automatic matches, 2 review suggestions, 1 unpaired order, and 1 unpaired settlement**. `reports/sample.json` includes the matching evidence, reason codes, amount totals, and canonical SHA-256 fingerprints of both inputs. The output write is atomic. For a command available elsewhere, run `python3 -m pip install .` and use `clearmatch` instead of `python3 -m clearmatch`.

Input CSV columns are `id,account,amount_cents,currency,date,reference`. Amounts are positive integer minor units (for USD, cents); dates are `YYYY-MM-DD`. IDs must be unique within each file. Account and currency must agree before a pair can even become a candidate.

## How the matching works

1. Settlements are indexed by `(account, currency)` and date. A binary search finds only settlements inside each order's configurable date window.
2. Candidates outside the amount tolerance are discarded. The remaining cost combines normalized reference similarity, day difference, and amount difference.
3. An automatic match must meet both a maximum cost and a minimum margin over competing candidates. Missing or unrelated references normally require review.
4. Eligible edges go through a **maximum-cardinality, minimum-cost bipartite matcher** using shortest augmenting paths. This avoids a greedy choice that could leave another order unmatched. Each account/currency group is solved independently.
5. Unresolved records receive separate, non-binding review suggestions. No order or settlement is assigned twice.

Run `python3 -m clearmatch --help` to adjust the date window, amount tolerance, and confidence gates. Review suggestions are **not** automatic ledger postings; the report intentionally keeps them separate.

## Verification and performance

```bash
python3 -W error -m unittest discover -s tests -v
python3 benchmarks/run.py --records 1000 --groups 100 --repeats 5
```

The 18 tests cover validation, deterministic output, partition boundaries, ambiguous matches, amount tolerance, a greedy-matching trap, CLI reports, and 40 small random graphs checked against a brute-force optimum. [GitHub Actions](.github/workflows/ci.yml) runs the tests, package installation, sample CLI, and a smaller benchmark on Python 3.11 and 3.13.

On this machine (macOS arm64, Python 3.14.2), the seeded benchmark reconciled **1,000 orders and 1,000 settlements**, generating **10,000 candidate edges** and 1,000 automatic matches in **59.153 ms median** over five timed runs (58.964–59.172 ms). This is a synthetic workload with 100 independent account groups and exact reference matches, not a production throughput claim. The script prints its own workload and environment so the result is reproducible and comparable on other machines.

## Scope and safety

ClearMatch is a demonstration, **not a bank-grade reconciliation or accounting system**. It handles one-to-one, positive-amount transactions in the same currency. It does not support partial settlements, split payouts, refunds, currency conversion, fee allocation, or legal/accounting rules. Reference scoring is a heuristic, and even an automatic match should be reviewed before any real financial action. Do not commit real transaction data or generated reports containing private IDs to a public repository.

For a resume, if relevant to the role:

> Built a deterministic Python transaction-reconciliation CLI using date-indexed candidate generation and min-cost bipartite matching, with ambiguity gates, auditable JSON reports, 18 automated tests, and a reproducible 10,000-edge synthetic benchmark.
