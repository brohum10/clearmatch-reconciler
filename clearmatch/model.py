"""Strict CSV input boundary; the example data is synthetic."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


class DataError(ValueError):
    """Input is malformed or ambiguous enough that matching must stop."""


FIELDS = {"id", "account", "amount_cents", "currency", "date", "reference"}


@dataclass(frozen=True)
class Record:
    id: str
    account: str
    amount_cents: int
    currency: str
    date: date
    reference: str


def read_records(path: str | Path) -> list[Record]:
    path = Path(path)
    records: list[Record] = []
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None or not FIELDS <= set(reader.fieldnames):
                raise DataError(f"{path}: expected columns {', '.join(sorted(FIELDS))}")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise DataError(f"{path}: duplicate CSV header")
            for line, row in enumerate(reader, start=2):
                if None in row or any(row.get(field) is None for field in FIELDS):
                    raise DataError(f"{path}:{line}: wrong number of columns")
                record_id = row["id"].strip()
                account = row["account"].strip()
                currency = row["currency"].strip().upper()
                reference = row["reference"].strip()
                if not record_id or not account:
                    raise DataError(f"{path}:{line}: id and account are required")
                if record_id in seen:
                    raise DataError(f"{path}:{line}: duplicate id {record_id}")
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    raise DataError(f"{path}:{line}: currency must be a three-letter code")
                amount_text = row["amount_cents"].strip()
                if not re.fullmatch(r"[1-9][0-9]*", amount_text):
                    raise DataError(f"{path}:{line}: amount_cents must be a positive integer")
                date_text = row["date"].strip()
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                    raise DataError(f"{path}:{line}: date must be YYYY-MM-DD")
                try:
                    booked = date.fromisoformat(date_text)
                except ValueError as exc:
                    raise DataError(f"{path}:{line}: invalid calendar date") from exc
                records.append(Record(record_id, account, int(amount_text), currency,
                                      booked, reference))
                seen.add(record_id)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataError(f"cannot read {path}: {exc}") from exc
    return records


def fingerprint(records: list[Record]) -> str:
    """Canonical digest, independent of CSV row order."""
    rows = [[item.id, item.account, item.amount_cents, item.currency,
             item.date.isoformat(), item.reference] for item in sorted(records, key=lambda item: item.id)]
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
