"""The Catalyst deployment descriptor must stay internally consistent.

These are static checks over the committed JSON — no network, no credentials.
They exist because a wrong descriptor fails silently: the nightly pipeline
simply never runs, and nothing in the application logs says so.

The specific defect pinned here: `circuits/nightly.json` once carried its own
top-level `schedule` block. A Catalyst Circuit has no schedule field — Cron is
the component that invokes a Circuit — so that schedule would never have
fired.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CATALYST_ROOT = Path(__file__).resolve().parents[3] / "catalyst"
DESCRIPTOR = CATALYST_ROOT / "catalyst.json"


@pytest.fixture(scope="module")
def descriptor() -> dict:
    return json.loads(DESCRIPTOR.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def circuits(descriptor) -> dict[str, dict]:
    loaded = {}
    for entry in descriptor.get("circuits", []):
        path = CATALYST_ROOT / entry["source"]
        loaded[entry["name"]] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


class TestCircuitsAreNotSelfScheduling:
    def test_no_circuit_declares_its_own_schedule(self, circuits):
        for name, circuit in circuits.items():
            assert "schedule" not in circuit, (
                f"Circuit {name!r} declares a 'schedule'. Circuits are invoked by "
                "Catalyst Cron; a schedule here is not a Circuits field and never fires."
            )

    def test_every_circuit_has_a_cron_that_invokes_it(self, descriptor, circuits):
        targeted = {
            entry.get("target")
            for entry in descriptor.get("cron", [])
            if entry.get("schedule_point") == "circuit"
        }
        for name in circuits:
            assert name in targeted, (
                f"Circuit {name!r} has no cron targeting it, so nothing will ever start it."
            )


class TestDescriptorReferentialIntegrity:
    def test_circuit_sources_exist(self, descriptor):
        for entry in descriptor.get("circuits", []):
            assert (CATALYST_ROOT / entry["source"]).is_file()

    def test_circuit_states_only_call_declared_functions(self, descriptor, circuits):
        declared = {fn["name"] for fn in descriptor.get("functions", [])}
        for name, circuit in circuits.items():
            for state_name, state in circuit["states"].items():
                if state.get("type") == "function":
                    assert state["function"] in declared, (
                        f"{name}.{state_name} calls undeclared function {state['function']!r}"
                    )

    def test_circuit_transitions_point_at_real_states(self, circuits):
        for name, circuit in circuits.items():
            states = circuit["states"]
            assert circuit["start_at"] in states
            for state_name, state in states.items():
                targets = []
                if "next" in state:
                    targets.append(state["next"])
                for choice in state.get("choices", []):
                    targets.append(choice["next"])
                if "default" in state:
                    targets.append(state["default"])
                for target in targets:
                    assert target in states, (
                        f"{name}.{state_name} transitions to unknown state {target!r}"
                    )

    def test_terminal_states_are_reachable_and_typed(self, circuits):
        for name, circuit in circuits.items():
            kinds = {state.get("type") for state in circuit["states"].values()}
            assert "succeed" in kinds, f"Circuit {name!r} has no success terminal state"


class TestDeployableSources:
    """Every component names a source directory that exists, and each one that
    cannot be deployed straight from the checkout says where it is staged from.

    The defect pinned here: `appsail/console` used to be listed as the thing to
    deploy, while `server.js` read the console build from `../../../frontend/dist`
    — outside the directory Catalyst ships. The service would deploy cleanly and
    then serve nothing. Naming a `deploy_source` and the `build` that produces it
    is what makes that reviewable rather than discovered in production.
    """

    def _components(self, descriptor):
        return [*descriptor.get("functions", []), *descriptor.get("appsail", [])]

    def test_every_component_source_directory_exists(self, descriptor):
        for entry in self._components(descriptor):
            source = CATALYST_ROOT / entry["source"]
            assert source.is_dir(), f"{entry['name']}: source {entry['source']!r} does not exist"

    def test_every_component_declares_how_its_artifact_is_built(self, descriptor):
        for entry in self._components(descriptor):
            assert entry.get("deploy_source"), f"{entry['name']}: no deploy_source declared"
            assert entry.get("build"), f"{entry['name']}: no build command declared"

    def test_every_declared_build_is_a_real_artifact_target(self, descriptor):
        import sys

        sys.path.insert(0, str(CATALYST_ROOT.parent / "scripts"))
        targets = set(__import__("build_catalyst_artifact").TARGETS)
        for entry in self._components(descriptor):
            build = entry["build"]
            assert "build_catalyst_artifact.py" in build, f"{entry['name']}: {build!r}"
            target = build.rsplit("--target", 1)[1].strip().split()[0]
            assert target in targets, (
                f"{entry['name']} builds --target {target!r}, which the artifact "
                f"script does not define (has: {sorted(targets)})"
            )
