"""Financial link analysis over the SYNTHETIC transaction extension.

The organiser's ER schema contains no financial table. Rather than inventing
source structure silently (architecture §15's stance), the platform models
transactions in ``ext_financial_transaction`` — a table whose name, repository
and every derived statement are marked as an extension, and whose evidence
carries ``Provenance.SYNTHETIC_EXTENSION`` so the console can badge it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

STRUCTURING_THRESHOLD = 200_000.0   # INR; the reporting threshold this mimics
STRUCTURING_BAND = 0.85             # transactions this close to it are notable


@dataclass(slots=True)
class CounterpartyFlow:
    ref: str
    label: str
    kind: str
    sent: float = 0.0
    received: float = 0.0
    txn_count: int = 0
    case_ids: list[int] = field(default_factory=list)
    txn_ids: list[str] = field(default_factory=list)

    @property
    def net(self) -> float:
        return round(self.received - self.sent, 2)


@dataclass(slots=True)
class FinancialSummary:
    subject_ref: str
    subject_label: str
    total_sent: float
    total_received: float
    counterparties: list[CounterpartyFlow]
    transactions: list[dict[str, Any]]
    patterns: list[dict[str, Any]]
    case_ids: list[int]
    is_extension: bool = True


class FinancialAnalyzer:
    """Deterministic aggregation. No inference beyond stated arithmetic."""

    def summarize(
        self,
        *,
        subject_ref: str,
        subject_label: str,
        transactions: Sequence[dict[str, Any]],
    ) -> FinancialSummary:
        flows: dict[str, CounterpartyFlow] = {}
        total_sent = 0.0
        total_received = 0.0
        case_ids: set[int] = set()

        for txn in transactions:
            amount = float(txn["amount"])
            case_id = txn.get("case_master_id")
            if case_id:
                case_ids.add(int(case_id))
            if str(txn["from_ref"]) == subject_ref:
                total_sent += amount
                flow = flows.setdefault(
                    str(txn["to_ref"]),
                    CounterpartyFlow(ref=str(txn["to_ref"]), label=str(txn["to_label"]), kind=str(txn["to_kind"])),
                )
                flow.received += amount
            elif str(txn["to_ref"]) == subject_ref:
                total_received += amount
                flow = flows.setdefault(
                    str(txn["from_ref"]),
                    CounterpartyFlow(ref=str(txn["from_ref"]), label=str(txn["from_label"]),
                                     kind=str(txn["from_kind"])),
                )
                flow.sent += amount
            else:
                continue
            flow.txn_count += 1
            flow.txn_ids.append(str(txn["txn_id"]))
            if case_id:
                flow.case_ids.append(int(case_id))

        ranked = sorted(flows.values(), key=lambda f: f.sent + f.received, reverse=True)
        return FinancialSummary(
            subject_ref=subject_ref,
            subject_label=subject_label,
            total_sent=round(total_sent, 2),
            total_received=round(total_received, 2),
            counterparties=ranked,
            transactions=list(transactions),
            patterns=self.detect_patterns(transactions),
            case_ids=sorted(case_ids),
        )

    def detect_patterns(self, transactions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Two arithmetic observations, stated as observations, not findings."""
        patterns: list[dict[str, Any]] = []

        near_threshold = [
            txn for txn in transactions
            if STRUCTURING_THRESHOLD * STRUCTURING_BAND <= float(txn["amount"]) < STRUCTURING_THRESHOLD
        ]
        if len(near_threshold) >= 3:
            patterns.append({
                "pattern": "amounts clustered just below the reporting threshold",
                "observation": (
                    f"{len(near_threshold)} transfers fall between "
                    f"₹{STRUCTURING_THRESHOLD * STRUCTURING_BAND:,.0f} and ₹{STRUCTURING_THRESHOLD:,.0f}"
                ),
                "txn_ids": [str(t["txn_id"]) for t in near_threshold],
                "case_ids": sorted({int(t["case_master_id"]) for t in near_threshold if t.get("case_master_id")}),
                "caveat": "This is an arithmetic observation about the recorded amounts, not a finding of structuring.",
            })

        by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for txn in transactions:
            by_day[(str(txn["txn_date"])[:10], str(txn["from_ref"]))].append(txn)
        rapid = [(key, items) for key, items in by_day.items() if len(items) >= 4]
        for (day, sender), items in rapid[:5]:
            patterns.append({
                "pattern": "several transfers from one party on a single day",
                "observation": f"{len(items)} transfers recorded from {items[0]['from_label']} on {day}",
                "txn_ids": [str(t["txn_id"]) for t in items],
                "case_ids": sorted({int(t["case_master_id"]) for t in items if t.get("case_master_id")}),
                "caveat": "Same-day volume is common in legitimate business activity; treat as a question, not an answer.",
            })
        return patterns

    def flow_chain(self, transactions: Sequence[dict[str, Any]], *, start_ref: str, max_depth: int = 3) -> list[list[dict[str, Any]]]:
        """Forward money-movement chains from a starting party.

        Chains are ordered in time (each hop must post-date the previous one),
        which is the only sense in which "flow" is meaningful here.
        """
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for txn in transactions:
            outgoing[str(txn["from_ref"])].append(txn)
        for items in outgoing.values():
            items.sort(key=lambda t: str(t["txn_date"]))

        chains: list[list[dict[str, Any]]] = []

        def walk(ref: str, path: list[dict[str, Any]], after: str) -> None:
            if len(path) >= max_depth:
                chains.append(list(path))
                return
            extended = False
            for txn in outgoing.get(ref, []):
                if str(txn["txn_date"]) <= after:
                    continue
                if any(existing["txn_id"] == txn["txn_id"] for existing in path):
                    continue
                extended = True
                walk(str(txn["to_ref"]), path + [txn], str(txn["txn_date"]))
            if not extended and path:
                chains.append(list(path))

        walk(start_ref, [], "")
        chains = [chain for chain in chains if len(chain) >= 2]
        chains.sort(key=len, reverse=True)
        return chains[:10]
