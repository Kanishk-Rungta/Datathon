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
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from ..analytics.stats import z_score

STRUCTURING_THRESHOLD = 200_000.0   # INR; the reporting threshold this mimics
STRUCTURING_BAND = 0.85             # transactions this close to it are notable

#: A hop only counts as part of a chain if it follows the previous one within
#: this many days. Without a bound, any two transfers months apart join into a
#: "chain", which describes nothing.
CHAIN_WINDOW_DAYS = 14

#: Degree at or above this percentile of the observed distribution is reported
#: as concentration. Published in the trace so the reader can disagree with it.
CONCENTRATION_PERCENTILE = 90.0

#: Below this many counterparties a percentile is not a distribution, it is an
#: artefact of a handful of points.
MIN_ACCOUNTS_FOR_CONCENTRATION = 8

#: Burst detection compares a day against the subject's own prior activity.
BURST_BASELINE_DAYS = 30
BURST_MIN_HISTORY = 5
BURST_Z_THRESHOLD = 2.5


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
class HopChain:
    """A time-ordered sequence of transfers, each within the chain window."""

    txn_ids: list[str]
    path: list[str]              # labels, in order, source → … → destination
    amounts: list[float]
    start_date: str
    end_date: str
    case_ids: list[int]

    @property
    def hops(self) -> int:
        return len(self.txn_ids)

    @property
    def amount_ratio(self) -> float:
        """Last hop's amount as a ratio of the first.

        Not a "retained fraction": a ratio above 1.0 is normal and simply means
        the later hop carried more than the earlier one, because the parties in
        a chain have other sources of funds. Calling it retention would imply
        the same money is being tracked from end to end, which these records
        cannot establish.
        """
        if not self.amounts or self.amounts[0] == 0:
            return 0.0
        return round(self.amounts[-1] / self.amounts[0], 4)


@dataclass(slots=True)
class Concentration:
    """An account whose in- or out-degree sits in the tail of the distribution."""

    ref: str
    label: str
    kind: str
    direction: str               # "fan-in" | "fan-out"
    counterparty_count: int
    txn_count: int
    total_amount: float
    threshold_degree: float
    percentile: float


@dataclass(slots=True)
class AmountBand:
    label: str
    lower: float
    upper: float | None
    count: int
    total: float

    def share_of(self, total_count: int) -> float:
        return round(self.count / total_count, 4) if total_count else 0.0


@dataclass(slots=True)
class TemporalBurst:
    """A day on which an account's activity stands out against its own history."""

    ref: str
    label: str
    day: str
    txn_count: int
    amount: float
    baseline_mean: float
    z_score: float
    baseline_days: int


@dataclass(slots=True)
class NetworkPosition:
    """Structural position in the money-flow graph. Position, not culpability."""

    ref: str
    label: str
    kind: str
    degree: int
    degree_centrality: float
    betweenness: float


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
    chains: list[HopChain] = field(default_factory=list)
    concentrations: list[Concentration] = field(default_factory=list)
    amount_bands: list[AmountBand] = field(default_factory=list)
    bursts: list[TemporalBurst] = field(default_factory=list)
    positions: list[NetworkPosition] = field(default_factory=list)
    #: True when any contributing row is a platform extension rather than
    #: source record data. Derived from the rows (see :func:`rows_are_extension`),
    #: never assumed — the default only covers a summary built from no rows at
    #: all, where labelling is the safe answer.
    is_extension: bool = True


def rows_are_extension(*row_groups: Sequence[dict[str, Any]] | None) -> bool:
    """Whether an aggregate over these rows must carry the extension marker.

    ``ext_financial_transaction`` declares an ``is_extension`` column, so the
    marker is a property of the data, not a constant. Every row is synthetic in
    this build, but hard-coding that meant a future approved ingestion would
    still have been labelled "(synthetic extension)" — and, worse, that the
    column the schema already defines was never read.

    The rule is deliberately one-directional: an aggregate is an extension if
    *any* contributing row is. Mixing one synthetic row into real data does not
    produce a real total, so the marker survives the mix. A missing column reads
    as an extension, because an unlabelled row is not evidence of provenance.
    """
    for rows in row_groups:
        for row in rows or ():
            flag = row.get("is_extension", 1)
            if flag is None or bool(int(flag)):
                return True
    return False


class FinancialAnalyzer:
    """Deterministic aggregation. No inference beyond stated arithmetic."""

    def summarize(
        self,
        *,
        subject_ref: str,
        subject_label: str,
        transactions: Sequence[dict[str, Any]],
        network_transactions: Sequence[dict[str, Any]] | None = None,
        subject_refs: Sequence[str] | None = None,
    ) -> FinancialSummary:
        """Summarise a subject's transfers.

        ``transactions`` are the subject's own, and drive the totals and the
        counterparty table. ``network_transactions`` — the subject plus their
        counterparties' other transfers — drive the *structural* analyses,
        because a chain, a concentration or a broker position is a property of
        a neighbourhood and is invisible in one account's rows alone. It
        defaults to ``transactions`` so a caller that has only the subject's
        rows still gets a correct, if narrower, answer.

        ``subject_refs`` is **every** source row this identity resolves to, not
        just the display one. Entity resolution routinely merges several
        ``curated_Accused`` rows into one person, and a transfer may be recorded
        against any of them. Matching on a single ref counted such a transfer in
        the transaction count while contributing nothing to the totals, so a
        person with real money movement could be reported as
        "1 transaction … ₹0 received and ₹0 sent".
        """
        network = list(network_transactions) if network_transactions is not None else list(transactions)
        owned = {str(r) for r in (subject_refs or [subject_ref]) if str(r)}
        flows: dict[str, CounterpartyFlow] = {}
        total_sent = 0.0
        total_received = 0.0
        case_ids: set[int] = set()

        for txn in transactions:
            amount = float(txn["amount"])
            case_id = txn.get("case_master_id")
            if case_id:
                case_ids.add(int(case_id))
            source_is_subject = str(txn["from_ref"]) in owned
            target_is_subject = str(txn["to_ref"]) in owned
            if source_is_subject and target_is_subject:
                # A transfer between two source rows that resolved to the same
                # person. Counting it would inflate both totals for money that
                # never left the subject.
                continue
            if source_is_subject:
                total_sent += amount
                flow = flows.setdefault(
                    str(txn["to_ref"]),
                    CounterpartyFlow(ref=str(txn["to_ref"]), label=str(txn["to_label"]), kind=str(txn["to_kind"])),
                )
                flow.received += amount
            elif target_is_subject:
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
            chains=self.hop_chains(network),
            concentrations=self.concentration(network),
            amount_bands=self.amount_distribution(transactions),
            bursts=self.temporal_bursts(network),
            positions=self.network_positions(network),
            is_extension=rows_are_extension(transactions, network),
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

    # ------------------------------------------------------------- chains
    def hop_chains(
        self,
        transactions: Sequence[dict[str, Any]],
        *,
        min_hops: int = 2,
        max_hops: int = 4,
        window_days: int = CHAIN_WINDOW_DAYS,
        limit: int = 5,
    ) -> list[HopChain]:
        """Money that moves onward through several parties in quick succession.

        Two constraints make this describe something real rather than any two
        transfers that happen to share an account:

        * each hop must **post-date** the previous one, and
        * each hop must fall within ``window_days`` of it.

        This is deliberately *not* called layering. A chain of onward transfers
        is a shape in the data; whether it was intended to obscure an origin is
        a question for an investigator, not an arithmetic result.
        """
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for txn in transactions:
            outgoing[str(txn["from_ref"])].append(txn)
        for items in outgoing.values():
            items.sort(key=lambda t: str(t["txn_date"]))

        chains: list[list[dict[str, Any]]] = []

        def walk(ref: str, path: list[dict[str, Any]]) -> None:
            if len(path) >= max_hops:
                chains.append(list(path))
                return
            extended = False
            for txn in outgoing.get(ref, []):
                if path:
                    previous = _parse_date(path[-1]["txn_date"])
                    current = _parse_date(txn["txn_date"])
                    if previous is None or current is None:
                        continue
                    if not (previous < current <= previous + timedelta(days=window_days)):
                        continue
                    # A party may appear twice, but the same transfer may not.
                    if any(existing["txn_id"] == txn["txn_id"] for existing in path):
                        continue
                extended = True
                walk(str(txn["to_ref"]), path + [txn])
            if not extended and len(path) >= min_hops:
                chains.append(list(path))

        for origin in outgoing:
            walk(origin, [])

        seen: set[tuple[str, ...]] = set()
        results: list[HopChain] = []
        for chain in sorted(chains, key=len, reverse=True):
            if len(chain) < min_hops:
                continue
            key = tuple(str(t["txn_id"]) for t in chain)
            if key in seen:
                continue
            seen.add(key)
            results.append(HopChain(
                txn_ids=list(key),
                path=[str(chain[0]["from_label"])] + [str(t["to_label"]) for t in chain],
                amounts=[float(t["amount"]) for t in chain],
                start_date=str(chain[0]["txn_date"])[:10],
                end_date=str(chain[-1]["txn_date"])[:10],
                case_ids=sorted({int(t["case_master_id"]) for t in chain if t.get("case_master_id")}),
            ))
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------ concentration
    def concentration(
        self,
        transactions: Sequence[dict[str, Any]],
        *,
        percentile: float = CONCENTRATION_PERCENTILE,
        limit: int = 5,
    ) -> list[Concentration]:
        """Accounts collecting from, or paying out to, unusually many others.

        The threshold is the given percentile of the *observed* degree
        distribution, not a fixed number, so it adapts to the dataset — and it
        is carried on every result so a reader can see what "unusual" meant.

        Below :data:`MIN_ACCOUNTS_FOR_CONCENTRATION` distinct accounts this
        returns nothing: a percentile over a handful of points describes the
        handful, not a pattern.
        """
        senders: dict[str, set[str]] = defaultdict(set)
        receivers: dict[str, set[str]] = defaultdict(set)
        meta: dict[str, tuple[str, str]] = {}
        counts: dict[tuple[str, str], int] = defaultdict(int)
        totals: dict[tuple[str, str], float] = defaultdict(float)

        for txn in transactions:
            source, target = str(txn["from_ref"]), str(txn["to_ref"])
            meta.setdefault(source, (str(txn["from_label"]), str(txn["from_kind"])))
            meta.setdefault(target, (str(txn["to_label"]), str(txn["to_kind"])))
            receivers[target].add(source)
            senders[source].add(target)
            amount = float(txn["amount"])
            counts[(target, "fan-in")] += 1
            totals[(target, "fan-in")] += amount
            counts[(source, "fan-out")] += 1
            totals[(source, "fan-out")] += amount

        if len(meta) < MIN_ACCOUNTS_FOR_CONCENTRATION:
            return []

        results: list[Concentration] = []
        for direction, mapping in (("fan-in", receivers), ("fan-out", senders)):
            degrees = sorted(len(peers) for peers in mapping.values())
            # The percentile sets the bar, but never below two counterparties:
            # a single transfer is not concentration in any dataset. The floor
            # also stops a long tail of degree-1 accounts dragging the
            # percentile down onto itself — with degrees [1,1,1,1,1,1,12] the
            # p90 is 1, which would otherwise discard the very hub the analysis
            # exists to surface.
            cutoff = max(_percentile(degrees, percentile), 2.0)
            for ref, peers in mapping.items():
                if len(peers) < cutoff:
                    continue
                label, kind = meta.get(ref, (ref, "unknown"))
                results.append(Concentration(
                    ref=ref, label=label, kind=kind, direction=direction,
                    counterparty_count=len(peers),
                    txn_count=counts[(ref, direction)],
                    total_amount=round(totals[(ref, direction)], 2),
                    threshold_degree=cutoff, percentile=percentile,
                ))
        results.sort(key=lambda c: (c.counterparty_count, c.total_amount), reverse=True)
        return results[:limit]

    # ------------------------------------------------------- distribution
    def amount_distribution(self, transactions: Sequence[dict[str, Any]]) -> list[AmountBand]:
        """Where the amounts sit relative to the reporting threshold.

        The existing near-threshold check answers "are there several?"; this
        answers "what does the whole distribution look like?", which is the
        context that stops a cluster being read as damning on its own.
        """
        edges: list[tuple[str, float, float | None]] = [
            ("below 25% of threshold", 0.0, STRUCTURING_THRESHOLD * 0.25),
            ("25–85% of threshold", STRUCTURING_THRESHOLD * 0.25, STRUCTURING_THRESHOLD * STRUCTURING_BAND),
            ("just below threshold", STRUCTURING_THRESHOLD * STRUCTURING_BAND, STRUCTURING_THRESHOLD),
            ("at or above threshold", STRUCTURING_THRESHOLD, None),
        ]
        bands = [AmountBand(label=label, lower=lower, upper=upper, count=0, total=0.0)
                 for label, lower, upper in edges]
        for txn in transactions:
            amount = float(txn["amount"])
            for band in bands:
                if amount >= band.lower and (band.upper is None or amount < band.upper):
                    band.count += 1
                    band.total = round(band.total + amount, 2)
                    break
        return bands

    # ------------------------------------------------------------- bursts
    def temporal_bursts(
        self,
        transactions: Sequence[dict[str, Any]],
        *,
        baseline_days: int = BURST_BASELINE_DAYS,
        threshold: float = BURST_Z_THRESHOLD,
        limit: int = 5,
    ) -> list[TemporalBurst]:
        """Days on which an account's own activity stands out from its history.

        Each account is compared against **itself**, never against other
        accounts: a busy merchant and a dormant personal account have no
        business sharing a baseline. This reuses the same ``z_score`` the
        early-warning analytics use, floor included.

        The baseline runs over **calendar** days, not just days the account
        transacted. Skipping the quiet days would hide dormancy, and dormancy
        is most of what makes a sudden day of activity worth a second look —
        the same reason the trend analytics densify a sparse month series with
        zeros rather than dropping the empty months.
        """
        per_account: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        labels: dict[str, str] = {}
        for txn in transactions:
            day = str(txn["txn_date"])[:10]
            amount = float(txn["amount"])
            for ref_key, label_key in (("from_ref", "from_label"), ("to_ref", "to_label")):
                ref = str(txn[ref_key])
                labels.setdefault(ref, str(txn[label_key]))
                per_account[ref][day].append(amount)

        results: list[TemporalBurst] = []
        for ref, by_day in per_account.items():
            active = sorted(d for d in by_day if _parse_date(d) is not None)
            if len(active) < BURST_MIN_HISTORY:
                continue
            for day in active:
                current = _parse_date(day)
                if current is None:
                    continue
                window_start = current - timedelta(days=baseline_days)
                if window_start < (_parse_date(active[0]) or current):
                    # Not enough recorded history behind this day to describe
                    # what "normal" looks like for the account yet.
                    continue
                baseline = [
                    float(len(by_day.get((window_start + timedelta(days=offset)).isoformat(), ())))
                    for offset in range(baseline_days)
                ]
                observed = float(len(by_day[day]))
                score = z_score(observed, baseline)
                if score < threshold:
                    continue
                results.append(TemporalBurst(
                    ref=ref, label=labels.get(ref, ref), day=day,
                    txn_count=len(by_day[day]),
                    amount=round(sum(by_day[day]), 2),
                    baseline_mean=round(sum(baseline) / len(baseline), 2),
                    z_score=round(score, 2), baseline_days=baseline_days,
                ))
        results.sort(key=lambda b: b.z_score, reverse=True)
        return results[:limit]

    # ---------------------------------------------------- network position
    def network_positions(
        self, transactions: Sequence[dict[str, Any]], *, limit: int = 5,
    ) -> list[NetworkPosition]:
        """Structural position of each account in the money-flow graph.

        Betweenness answers "how often does money pass *through* this party on
        the shortest route between two others" — a broker-shaped position. It
        describes the recorded transfers and nothing else: an account can sit
        between two others for entirely ordinary reasons, so this is labelled
        as position and never as culpability.
        """
        import networkx as nx

        graph = nx.Graph()
        meta: dict[str, tuple[str, str]] = {}
        for txn in transactions:
            source, target = str(txn["from_ref"]), str(txn["to_ref"])
            meta.setdefault(source, (str(txn["from_label"]), str(txn["from_kind"])))
            meta.setdefault(target, (str(txn["to_label"]), str(txn["to_kind"])))
            graph.add_edge(source, target)

        if graph.number_of_nodes() < 3:
            return []

        degree_centrality = nx.degree_centrality(graph)
        betweenness = nx.betweenness_centrality(graph)
        positions = [
            NetworkPosition(
                ref=node, label=meta.get(node, (node, "unknown"))[0],
                kind=meta.get(node, (node, "unknown"))[1],
                degree=graph.degree(node),
                degree_centrality=round(degree_centrality.get(node, 0.0), 4),
                betweenness=round(betweenness.get(node, 0.0), 4),
            )
            for node in graph.nodes
        ]
        positions.sort(key=lambda p: (p.betweenness, p.degree), reverse=True)
        return positions[:limit]



def _parse_date(value: Any) -> date | None:
    """Parse a stored transaction date, tolerating a full timestamp."""
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    """Nearest-rank percentile.

    Nearest-rank rather than interpolated because the values here are integer
    degrees: an interpolated cutoff of 3.4 counterparties is not a quantity
    anything in this dataset can have, and rounding it later would hide which
    convention produced the threshold.
    """
    if not sorted_values:
        return 0.0
    rank = max(1, int(round(percentile / 100.0 * len(sorted_values))))
    return float(sorted_values[min(rank, len(sorted_values)) - 1])
