"""Command line entry points.

Everything the scripts do is available here, so an operator never has to guess
which Python incantation seeds a database or refreshes the graph.

    python -m ksp_cip.cli seed --cases 4200
    python -m ksp_cip.cli refresh
    python -m ksp_cip.cli check
    python -m ksp_cip.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import get_settings
from .interface.container import build_container


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def command_seed(args: argparse.Namespace) -> int:
    container = build_container()
    summary = container.seeder.run(
        target_cases=args.cases, months=args.months, reset=args.reset
    )
    manifest = summary.pop("manifest", {})
    _print(summary)
    print(
        f"\nPlanted signals (assert these are found again):\n"
        f"  hotspots : {[h['district_name'] for h in manifest.get('hotspots', [])]}\n"
        f"  surge    : {manifest.get('surge', {}).get('district_name')} / "
        f"{manifest.get('surge', {}).get('crime_sub_head_name')}\n"
        f"  ring     : {len(manifest.get('ring', {}).get('members', []))} people, "
        f"{manifest.get('ring', {}).get('case_count', 0)} cases\n"
    )
    # No URL: seeding does not choose the serve port, and printing 8000 here
    # was wrong whenever the caller used another one (`cip.py run --port`,
    # `serve --port`). ASCII only, because a Windows console defaults to cp1252
    # and renders an em dash as a replacement character.
    print("Start the platform with `python cip.py` - demo accounts are listed on the login screen.")
    return 0


def command_refresh(args: argparse.Namespace) -> int:
    container = build_container()
    transactions = container.financial.all_transactions()
    report = container.refresher.refresh_all(transactions=transactions)
    _print(report.as_dict())
    return 0


def command_check(args: argparse.Namespace) -> int:
    """Report what the platform can and cannot currently do, honestly."""
    container = build_container()
    health = container.health()
    findings = container.control.dq_summary()
    _print({
        "health": health,
        "data_quality": findings,
        "graph": container.graph.stats() if health["cases"] else {"nodes": 0, "edges": 0},
        "entity_resolution": container.identities.link_stats(),
        "retrieval_documents": container.retrieval.document_count,
        "language_provider": container.language.provider_name,
        "language_full_fidelity": container.language.is_full_fidelity,
        "llm_provider": str(container.settings.llm_provider),
    })
    if not health["seeded"]:
        print("\nNo data yet. Run: python -m ksp_cip.cli seed", file=sys.stderr)
        return 1
    return 0


def command_dq(args: argparse.Namespace) -> int:
    container = build_container()
    findings = container.dq.run(batch_id="manual")
    failed = [f for f in findings if not f.passed]
    _print([
        {"check": f.check_name, "table": f.table_name, "severity": f.severity,
         "passed": f.passed, "observed": f.observed, "threshold": f.threshold}
        for f in findings
    ])
    return 1 if any(f.severity == "blocking" for f in failed) else 0


def command_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .interface.api.main import get_app

    uvicorn.run(get_app(), host=args.host, port=args.port)
    return 0


def command_config(args: argparse.Namespace) -> int:
    settings = get_settings()
    redacted = settings.model_dump()
    for key in list(redacted):
        if any(marker in key for marker in ("secret", "key", "token", "password")):
            redacted[key] = "***" if redacted[key] else None
    # Problems name the offending *setting*, never its value, so this output is
    # safe to paste into a ticket.
    problems = settings.deployment_problems()
    _print({
        "settings": redacted,
        "deployable": not problems,
        "problems": problems,
    })
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ksp_cip", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="generate synthetic data and build all intelligence")
    seed.add_argument("--cases", type=int, default=4200)
    seed.add_argument("--months", type=int, default=30)
    seed.add_argument("--reset", action="store_true", help="truncate before seeding")
    seed.set_defaults(func=command_seed)

    refresh = sub.add_parser("refresh", help="rebuild derived intelligence from curated data")
    refresh.set_defaults(func=command_refresh)

    check = sub.add_parser("check", help="report platform state and capabilities")
    check.set_defaults(func=command_check)

    dq = sub.add_parser("dq", help="run the data quality suite")
    dq.set_defaults(func=command_dq)

    serve = sub.add_parser("serve", help="run the API and console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=command_serve)

    config = sub.add_parser("config", help="print effective configuration (secrets redacted)")
    config.set_defaults(func=command_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
