"""Indexed candidate generation and deterministic, one-to-one reconciliation."""

from __future__ import annotations

import heapq
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass

from .model import DataError, Record, fingerprint


@dataclass(frozen=True)
class ReconcileConfig:
    date_window_days: int = 3
    amount_tolerance_cents: int = 0
    max_auto_cost: int = 50
    min_confidence_margin: int = 15

    def __post_init__(self) -> None:
        if min(self.date_window_days, self.amount_tolerance_cents,
               self.max_auto_cost, self.min_confidence_margin) < 0:
            raise ValueError("reconciliation limits must be nonnegative")


@dataclass(frozen=True)
class Candidate:
    order_id: str
    settlement_id: str
    cost: int
    date_delta_days: int
    amount_delta_cents: int
    reference_signal: str

    def as_dict(self, margin: int | None) -> dict:
        return {
            "order_id": self.order_id,
            "settlement_id": self.settlement_id,
            "cost": self.cost,
            "date_delta_days": self.date_delta_days,
            "amount_delta_cents": self.amount_delta_cents,
            "reference_signal": self.reference_signal,
            "confidence_margin": margin,
        }


@dataclass
class Edge:
    to: int
    reverse: int
    capacity: int
    cost: int


def _edge(graph: list[list[Edge]], start: int, end: int, cost: int) -> Edge:
    forward = Edge(end, len(graph[end]), 1, cost)
    backward = Edge(start, len(graph[start]), 0, -cost)
    graph[start].append(forward)
    graph[end].append(backward)
    return forward


def _min_cost_maximum_matching(candidates: list[Candidate]) -> list[Candidate]:
    """Successive shortest augmenting paths: maximize pairs, then minimize cost."""
    if not candidates:
        return []
    left_ids = sorted({candidate.order_id for candidate in candidates})
    right_ids = sorted({candidate.settlement_id for candidate in candidates})
    left = {value: index + 1 for index, value in enumerate(left_ids)}
    right = {value: index + 1 + len(left_ids) for index, value in enumerate(right_ids)}
    source = 0
    sink = 1 + len(left_ids) + len(right_ids)
    graph: list[list[Edge]] = [[] for _ in range(sink + 1)]
    for value in left_ids:
        _edge(graph, source, left[value], 0)
    for value in right_ids:
        _edge(graph, right[value], sink, 0)
    tracked: list[tuple[Candidate, Edge]] = []
    for candidate in sorted(candidates, key=lambda item: (item.cost, item.order_id,
                                                            item.settlement_id)):
        tracked.append((candidate, _edge(graph, left[candidate.order_id],
                                          right[candidate.settlement_id], candidate.cost)))

    potential = [0] * len(graph)
    infinity = 10**18
    while True:
        distance = [infinity] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        distance[source] = 0
        queue = [(0, source)]
        while queue:
            current, vertex = heapq.heappop(queue)
            if current != distance[vertex]:
                continue
            for index, edge in enumerate(graph[vertex]):
                if not edge.capacity:
                    continue
                reduced = edge.cost + potential[vertex] - potential[edge.to]
                proposed = current + reduced
                if proposed < distance[edge.to]:
                    distance[edge.to] = proposed
                    previous[edge.to] = (vertex, index)
                    heapq.heappush(queue, (proposed, edge.to))
        if previous[sink] is None:
            break
        for vertex, value in enumerate(distance):
            if value < infinity:
                potential[vertex] += value
        vertex = sink
        while vertex != source:
            parent, index = previous[vertex]  # type: ignore[misc]
            edge = graph[parent][index]
            edge.capacity -= 1
            graph[vertex][edge.reverse].capacity += 1
            vertex = parent
    return sorted((candidate for candidate, edge in tracked if edge.capacity == 0),
                  key=lambda item: (item.order_id, item.settlement_id))


def _match_partitions(candidates: list[Candidate], orders: dict[str, Record]) -> list[Candidate]:
    groups: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for item in candidates:
        order = orders[item.order_id]
        groups[(order.account, order.currency)].append(item)
    matched = []
    for key in sorted(groups):
        matched.extend(_min_cost_maximum_matching(groups[key]))
    return sorted(matched, key=lambda item: (item.order_id, item.settlement_id))


def _reference_signal(first: str, second: str) -> tuple[str, int]:
    a = re.sub(r"[^A-Z0-9]", "", first.upper())
    b = re.sub(r"[^A-Z0-9]", "", second.upper())
    if a and b and a == b:
        return "exact", 0
    if a and b and min(len(a), len(b)) >= 4 and (a in b or b in a):
        return "partial", 25
    if not a or not b:
        return "missing", 60
    return "different", 80


def _candidates(orders: list[Record], settlements: list[Record],
                config: ReconcileConfig) -> list[Candidate]:
    index: dict[tuple[str, str], list[tuple[int, str, Record]]] = defaultdict(list)
    for settlement in settlements:
        index[(settlement.account, settlement.currency)].append(
            (settlement.date.toordinal(), settlement.id, settlement))
    dates: dict[tuple[str, str], list[int]] = {}
    for key, group in index.items():
        group.sort(key=lambda item: (item[0], item[1]))
        dates[key] = [item[0] for item in group]

    result: list[Candidate] = []
    for order in sorted(orders, key=lambda item: item.id):
        key = (order.account, order.currency)
        group = index.get(key, [])
        days = dates.get(key, [])
        ordinal = order.date.toordinal()
        start = bisect_left(days, ordinal - config.date_window_days)
        stop = bisect_right(days, ordinal + config.date_window_days)
        for booked, _, settlement in group[start:stop]:
            amount_delta = abs(order.amount_cents - settlement.amount_cents)
            if amount_delta > config.amount_tolerance_cents:
                continue
            signal, reference_penalty = _reference_signal(order.reference, settlement.reference)
            day_delta = abs(ordinal - booked)
            cost = reference_penalty + day_delta * 8 + amount_delta * 5
            result.append(Candidate(order.id, settlement.id, cost, day_delta,
                                    amount_delta, signal))
    return result


def _margins(candidates: list[Candidate]) -> dict[tuple[str, str], int | None]:
    by_order: dict[str, list[Candidate]] = defaultdict(list)
    by_settlement: dict[str, list[Candidate]] = defaultdict(list)
    for item in candidates:
        by_order[item.order_id].append(item)
        by_settlement[item.settlement_id].append(item)
    margins: dict[tuple[str, str], int | None] = {}
    for item in candidates:
        alternatives = [other.cost - item.cost
                        for other in by_order[item.order_id]
                        if other.settlement_id != item.settlement_id]
        alternatives.extend(other.cost - item.cost
                            for other in by_settlement[item.settlement_id]
                            if other.order_id != item.order_id)
        margins[(item.order_id, item.settlement_id)] = min(alternatives) if alternatives else None
    return margins


def reconcile(orders: list[Record], settlements: list[Record],
              config: ReconcileConfig = ReconcileConfig()) -> dict:
    """Return auto matches, review suggestions, and unmatched records."""
    if len({item.id for item in orders}) != len(orders):
        raise DataError("duplicate order id")
    if len({item.id for item in settlements}) != len(settlements):
        raise DataError("duplicate settlement id")
    order_lookup = {item.id: item for item in orders}
    settlement_lookup = {item.id: item for item in settlements}
    candidates = _candidates(orders, settlements, config)
    margins = _margins(candidates)
    eligible = [item for item in candidates
                if item.cost <= config.max_auto_cost
                and (margins[(item.order_id, item.settlement_id)] is None
                     or margins[(item.order_id, item.settlement_id)] >= config.min_confidence_margin)]
    auto = _match_partitions(eligible, order_lookup)
    used_orders = {item.order_id for item in auto}
    used_settlements = {item.settlement_id for item in auto}
    remaining = [item for item in candidates if item.order_id not in used_orders
                 and item.settlement_id not in used_settlements]
    review = _match_partitions(remaining, order_lookup)
    proposed_orders = {item.order_id for item in review}
    proposed_settlements = {item.settlement_id for item in review}
    def review_entry(item: Candidate) -> dict:
        entry = item.as_dict(margins[(item.order_id, item.settlement_id)])
        reasons = []
        if item.cost > config.max_auto_cost:
            reasons.append("low_confidence")
        margin = margins[(item.order_id, item.settlement_id)]
        if margin is not None and margin < config.min_confidence_margin:
            reasons.append("competing_candidate")
        entry["reasons"] = reasons
        return entry

    return {
        "config": {
            "date_window_days": config.date_window_days,
            "amount_tolerance_cents": config.amount_tolerance_cents,
            "max_auto_cost": config.max_auto_cost,
            "min_confidence_margin": config.min_confidence_margin,
        },
        "fingerprints": {"orders_sha256": fingerprint(orders),
                         "settlements_sha256": fingerprint(settlements)},
        "counts": {"orders": len(orders), "settlements": len(settlements),
                   "candidates": len(candidates), "automatic_matches": len(auto),
                   "review_suggestions": len(review)},
        "amounts_cents": {
            "orders_total": sum(item.amount_cents for item in orders),
            "settlements_total": sum(item.amount_cents for item in settlements),
            "automatically_matched_orders": sum(order_lookup[item.order_id].amount_cents
                                                for item in auto),
            "automatically_matched_settlements": sum(
                settlement_lookup[item.settlement_id].amount_cents for item in auto),
        },
        "automatic_matches": [item.as_dict(margins[(item.order_id, item.settlement_id)])
                              for item in auto],
        "review_suggestions": [review_entry(item) for item in review],
        "unpaired_orders": sorted(item.id for item in orders
                                  if item.id not in used_orders | proposed_orders),
        "unpaired_settlements": sorted(item.id for item in settlements
                                       if item.id not in used_settlements | proposed_settlements),
    }
