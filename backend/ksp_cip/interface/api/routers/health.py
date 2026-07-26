"""Liveness, readiness and self-description."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..deps import ContainerDep

router = APIRouter(tags=["health"])


@router.get("/health")
def health(container: ContainerDep) -> dict[str, Any]:
    """Liveness: the process is up and can answer. Nothing here can fail the
    process into a restart loop over a degraded optional dependency — that
    distinction belongs to ``/health/ready``."""
    return container.health()


@router.get("/health/ready")
def ready(container: ContainerDep) -> dict[str, Any]:
    """Readiness: it is safe to route traffic here.

    Per implementationv2-phases-0-2.md P1-05: this must fail when the
    selected system-of-record backend is unavailable or misconfigured, and it
    separately reports (without failing on) optional providers running in a
    degraded fallback mode, so a missing Bhashini/hosted-LLM credential is
    visible without taking the whole deployment out of rotation.
    """
    state = container.health()
    problems = container.settings.deployment_problems()
    state["configuration_valid"] = not problems
    state["configuration_problems"] = problems

    # Note: the local deterministic LLM is the platform's deliberate,
    # trustworthy default (ADR-0003), not a degraded fallback -- it is not
    # listed here. The offline Kannada glossary genuinely is a reduced-fidelity
    # substitute for Bhashini, which is why only it appears.
    degraded: list[str] = []
    if not container.language.is_full_fidelity:
        degraded.append("language: running on the offline glossary fallback, not Bhashini")
    state["degraded_optional_services"] = degraded

    state["ready"] = state["status"] == "ok" and state["seeded"] and not problems
    if problems:
        state["hint"] = "Configuration is not deployable; see configuration_problems."
    elif not state["seeded"]:
        state["hint"] = "Run `python -m ksp_cip.cli seed` (or POST /admin/seed) to load data."
    return state


@router.get("/capabilities")
def capabilities(container: ContainerDep) -> dict[str, Any]:
    """What this deployment can and cannot do, stated plainly.

    The console reads this to badge degraded capabilities rather than pretending
    everything is fully operational — for instance, translation quality when no
    Bhashini credentials are configured.
    """
    settings = container.settings
    return {
        "agents": [
            "SupervisorAgent", "DataRetrievalAgent", "CrimeAnalyticsAgent",
            "NetworkIntelligenceAgent", "InvestigationSupportAgent",
        ],
        "languages": ["en", "kn"],
        "language_provider": container.language.provider_name,
        "language_full_fidelity": container.language.is_full_fidelity,
        "language_notice": (
            None if container.language.is_full_fidelity else
            "Kannada support is running on the offline glossary fallback. It covers police "
            "terminology and place names but is not full machine translation. Configure Bhashini "
            "credentials for production-quality translation."
        ),
        "llm_provider": str(settings.llm_provider),
        "llm_is_local_deterministic": container.llm.is_local,
        "llm_notice": (
            "The local provider composes answers deterministically from retrieved facts and never "
            "invents content. Hosted providers are used only to rephrase, and every rephrasing is "
            "verified against the original figures and citations."
        ),
        "embedding_model": settings.embedding_model_name,
        "graph_backend": "networkx-in-memory",
        "datastore_backend": str(settings.datastore_backend),
        "financial_data": "synthetic extension — not part of the source FIR schema",
        "voice_input": {
            "browser_speech_api": True,
            "server_side_asr": container.language.is_full_fidelity,
        },
    }
