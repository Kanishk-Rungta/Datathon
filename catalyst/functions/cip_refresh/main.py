"""Catalyst event function running one pipeline stage.

Stages map exactly to the local pipeline classes. The Circuit decides *when*
and *in what order*; this function decides nothing.

Handler shape (``def handler(event, context)``) matches Zoho's documented
Event Function contract — see ``docs/deployment/catalyst-runtime.md`` for the
verification behind that statement. Nothing here should be changed to work
around a Catalyst runtime detail without updating that document too.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# `_bootstrap.py` sits alongside this file once staged (see
# scripts/build_catalyst_artifact.py); in the repo checkout it lives two
# levels up at `catalyst/_bootstrap.py`. Try the staged layout first.
sys.path.insert(0, str(_HERE))
try:
    from _bootstrap import bootstrap
except ImportError:
    sys.path.insert(0, str(_HERE.parents[1]))
    from _bootstrap import bootstrap

bootstrap(__file__)

from ksp_cip.interface.container import build_container  # noqa: E402

VALID_STAGES = ("ingest", "data_quality", "intelligence", "retention")


def run_stage(stage: str) -> dict:
    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown pipeline stage: {stage!r}. Must be one of {VALID_STAGES}.")

    correlation_id = uuid.uuid4().hex
    started = time.time()
    container = build_container()
    control = container.control

    # A durable run-start record so a stuck or failed invocation is visible in
    # the control tables rather than only in transient function logs.
    run_started = {
        "batch_id": f"refresh-{stage}-{correlation_id}",
        "source_table": f"stage:{stage}",
        "object_key": "",
        "row_count": 0,
        "min_pk": None,
        "max_pk": None,
        "content_sha256": None,
        "status": "RECEIVED",
    }
    try:
        control.register_batch(run_started)
    except Exception:  # noqa: BLE001 - a control-table hiccup must not block the stage itself
        pass

    try:
        result = _run_stage(stage, container)
    except Exception as exc:  # noqa: BLE001 - convert to a bounded, durable error category
        try:
            control.mark_batch(run_started["batch_id"], "FAILED", error=type(exc).__name__)
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        try:
            control.mark_batch(run_started["batch_id"], "LOADED")
        except Exception:  # noqa: BLE001
            pass
        result["correlation_id"] = correlation_id
        result["duration_seconds"] = round(time.time() - started, 3)
        return result


def _run_stage(stage: str, container) -> dict:
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
