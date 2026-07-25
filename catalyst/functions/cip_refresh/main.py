"""Catalyst event function running one pipeline stage.

Stages map exactly to the local pipeline classes. The Circuit decides *when*
and *in what order*; this function decides nothing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("KSPCIP_DATASTORE_BACKEND", "catalyst")
os.environ.setdefault("KSPCIP_ENVIRONMENT", "catalyst")

from ksp_cip.interface.container import build_container  # noqa: E402


def run_stage(stage: str) -> dict:
    container = build_container()

    if stage == "ingest":
        # Batches are landed in Stratus by the Sync Agent; the loader consumes
        # whatever is present and is safe to re-run on the same batch.
        from ksp_cip.application.pipeline import BatchWriter, Loader

        writer = BatchWriter(container.filestore, container.control)
        loader = Loader(container.store, container.control, writer)
        pending = container.control.batches(limit=200)
        descriptors = [
            type("D", (), {
                "batch_id": row["batch_id"], "table": row["source_table"],
                "key": row["object_key"], "row_count": row["row_count"],
            })()
            for row in pending if row.get("status") == "landed"
        ]
        return {"stage": stage, **loader.load(descriptors)}

    if stage == "data_quality":
        findings = container.dq.run(batch_id="scheduled")
        blocking = [f.check_name for f in findings if not f.passed and f.severity == "blocking"]
        return {
            "stage": stage,
            "checks": len(findings),
            "passed": sum(1 for f in findings if f.passed),
            "blocking_failures": len(blocking),
            "failed_checks": blocking,
        }

    if stage == "intelligence":
        transactions = container.financial.all_transactions()
        report = container.refresher.refresh_all(transactions=transactions)
        return {"stage": stage, **report.as_dict()}

    if stage == "retention":
        return {"stage": stage, **container.memory.purge_expired()}

    raise ValueError(f"Unknown pipeline stage: {stage}")


def handler(event, context):  # pragma: no cover - Catalyst runtime shim
    payload = event if isinstance(event, dict) else json.loads(event or "{}")
    return run_stage(str(payload.get("stage", "intelligence")))
