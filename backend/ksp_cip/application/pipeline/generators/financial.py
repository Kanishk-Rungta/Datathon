"""SYNTHETIC EXTENSION — financial transactions.

The organiser's FIR schema has no financial table. This generator produces
``ext_financial_transaction`` rows so the money-flow capability can be
demonstrated end to end. Every row it writes carries ``is_extension = 1`` and
every statement derived from it is marked as an extension in the UI, the PDF
and the evidence chain.

Transactions are planted around economic-offence cases and around the network
ring, so the graph has money edges that coincide with co-accused edges.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any, Sequence

from .cases import GeneratedCase

ECONOMIC_SUB_HEADS = {401, 402, 403, 404}
CHANNELS = ["UPI", "IMPS", "NEFT", "cash deposit", "wallet transfer", "RTGS"]
ENTITY_KINDS = ["trading firm", "shell company", "money exchange", "retail merchant", "current account"]
ENTITY_PREFIXES = ["Shree", "Sri", "Nandi", "Kaveri", "Tunga", "Malnad", "Deccan", "Sahyadri", "Vidhana"]
ENTITY_SUFFIXES = ["Traders", "Enterprises", "Agencies", "Distributors", "Ventures", "Associates"]


def generate_transactions(
    cases: Sequence[GeneratedCase],
    rng: random.Random,
    *,
    anchor: date,
    entity_count: int = 40,
    burst_spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate the synthetic transaction extension.

    If ``burst_spec`` is passed it is **populated** with the account and day
    given a deliberate spike, so an integration test can assert the burst
    analysis recovers it — the same planted-signal discipline the surge,
    hotspots and network ring already use. Without a planted burst the ordinary
    generator never produces more than two transfers per account per day, so
    the analysis would have nothing to find and the code path would be untested.
    """
    entities = [
        {
            "ref": f"E{index:05d}",
            "label": f"{rng.choice(ENTITY_PREFIXES)} {rng.choice(ENTITY_SUFFIXES)}",
            "kind": rng.choice(ENTITY_KINDS),
        }
        for index in range(1, entity_count + 1)
    ]

    rows: list[dict[str, Any]] = []
    counter = 1
    for generated in cases:
        sub_head = generated.case["CrimeMinorHeadID"]
        multi_accused = len(generated.accused) >= 2
        if sub_head not in ECONOMIC_SUB_HEADS and not (multi_accused and rng.random() < 0.06):
            continue
        if not generated.accused:
            continue

        registered = date.fromisoformat(generated.case["CrimeRegisteredDate"])
        case_id = generated.case["CaseMasterID"]
        transfer_count = rng.randint(2, 7)
        for _ in range(transfer_count):
            accused = rng.choice(generated.accused)
            entity = rng.choice(entities)
            outbound = rng.random() < 0.62
            amount = _amount(rng)
            txn_date = registered - timedelta(days=rng.randint(0, 90))
            if txn_date > anchor:
                txn_date = anchor
            source = (
                ("accused", str(accused["AccusedMasterID"]), accused["AccusedName"])
                if outbound else ("entity", entity["ref"], entity["label"])
            )
            target = (
                ("entity", entity["ref"], entity["label"])
                if outbound else ("accused", str(accused["AccusedMasterID"]), accused["AccusedName"])
            )
            rows.append({
                "txn_id": f"TX{counter:08d}",
                "case_master_id": case_id,
                "from_kind": source[0], "from_ref": source[1], "from_label": source[2],
                "to_kind": target[0], "to_ref": target[1], "to_label": target[2],
                "amount": amount,
                "currency": "INR",
                "txn_date": txn_date.isoformat(),
                "channel": rng.choice(CHANNELS),
                "is_extension": 1,
            })
            counter += 1

        # Plant a short onward chain so flow_chain has something to find.
        if rng.random() < 0.30 and len(entities) >= 3:
            hop_a, hop_b = rng.sample(entities, k=2)
            chain_date = registered - timedelta(days=rng.randint(1, 30))
            amount = _amount(rng, large=True)
            for source_entity, target_entity, offset in (
                (hop_a, hop_b, 0), (hop_b, rng.choice(entities), 2)
            ):
                rows.append({
                    "txn_id": f"TX{counter:08d}",
                    "case_master_id": case_id,
                    "from_kind": "entity", "from_ref": source_entity["ref"],
                    "from_label": source_entity["label"],
                    "to_kind": "entity", "to_ref": target_entity["ref"],
                    "to_label": target_entity["label"],
                    "amount": round(amount * (0.92 if offset else 1.0), 2),
                    "currency": "INR",
                    "txn_date": (chain_date + timedelta(days=offset)).isoformat(),
                    "channel": rng.choice(CHANNELS),
                    "is_extension": 1,
                })
                counter += 1

    # A deliberate spike: one account, one day, well above its own norm, and a
    # quiet run-up before it so the account has a dormant baseline to stand out
    # against. Planted last so it cannot perturb anything above.
    if burst_spec is not None and rows and entities:
        burst_entity = entities[0]
        burst_day = anchor - timedelta(days=45)
        # Sparse prior activity establishes "normal" for this account.
        for offset in (40, 33, 26, 19, 12):
            partner = entities[(offset % (len(entities) - 1)) + 1]
            rows.append(_entity_row(counter, burst_entity, partner,
                                    burst_day - timedelta(days=offset), _amount(rng)))
            counter += 1
        burst_count = 9
        for index in range(burst_count):
            partner = entities[(index % (len(entities) - 1)) + 1]
            rows.append(_entity_row(counter, burst_entity, partner, burst_day, _amount(rng)))
            counter += 1
        burst_spec.update({
            "ref": burst_entity["ref"],
            "label": burst_entity["label"],
            "day": burst_day.isoformat(),
            "txn_count": burst_count,
        })
    return rows


def _entity_row(counter: int, source: dict[str, Any], target: dict[str, Any],
                txn_date: date, amount: float) -> dict[str, Any]:
    return {
        "txn_id": f"TX{counter:08d}",
        "case_master_id": None,
        "from_kind": "entity", "from_ref": source["ref"], "from_label": source["label"],
        "to_kind": "entity", "to_ref": target["ref"], "to_label": target["label"],
        "amount": amount,
        "currency": "INR",
        "txn_date": txn_date.isoformat(),
        "channel": "NEFT",
        "is_extension": 1,
    }


def _amount(rng: random.Random, *, large: bool = False) -> float:
    if large:
        return float(rng.randrange(300_000, 4_000_000, 5_000))
    roll = rng.random()
    if roll < 0.18:
        # Deliberately just under the reporting threshold so the structuring
        # observation has real material to describe.
        return float(rng.randrange(170_000, 199_000, 500))
    if roll < 0.55:
        return float(rng.randrange(2_000, 60_000, 500))
    return float(rng.randrange(60_000, 900_000, 1_000))
