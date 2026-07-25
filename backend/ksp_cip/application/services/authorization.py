"""Authorization — the policy decision point.

Two layers, deliberately (architecture §12.2):

1. **Capability check.** ``Principal.require(permission)`` at the API and agent
   boundary.
2. **Scope injection.** Every repository query receives a :class:`UnitScope`
   and folds it into the SQL predicate. A caller that forgets to check a
   capability still cannot read rows outside its unit subtree.

Field-level rules live here too: the policymaker role never sees individual
identity fields, and caste/religion are only ever readable with an explicit
permission and only in aggregate (plan §9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ...domain.enums import Permission, Role
from ...domain.errors import AuthorizationError
from ...domain.models import Principal, UnitScope
from ...infrastructure.db.repositories import ReferenceRepository

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.INVESTIGATOR: frozenset({
        Permission.READ_CASE_DETAIL,
        Permission.READ_PERSON_IDENTITY,
        Permission.READ_AGGREGATES,
        Permission.USE_GRAPH_TOOLS,
        Permission.USE_OFFENDER_PROFILING,
        Permission.USE_FINANCIAL_TOOLS,
        Permission.EXPORT_PDF,
    }),
    Role.ANALYST: frozenset({
        Permission.READ_CASE_DETAIL,
        Permission.READ_PERSON_IDENTITY,
        Permission.READ_AGGREGATES,
        Permission.READ_STATEWIDE,
        Permission.USE_GRAPH_TOOLS,
        Permission.USE_OFFENDER_PROFILING,
        Permission.EXPORT_PDF,
    }),
    Role.SUPERVISOR: frozenset({
        Permission.READ_CASE_DETAIL,
        Permission.READ_PERSON_IDENTITY,
        Permission.READ_SENSITIVE_DEMOGRAPHICS,
        Permission.READ_AGGREGATES,
        Permission.READ_STATEWIDE,
        Permission.USE_GRAPH_TOOLS,
        Permission.USE_OFFENDER_PROFILING,
        Permission.USE_FINANCIAL_TOOLS,
        Permission.READ_AUDIT,
        Permission.EXPORT_PDF,
    }),
    Role.POLICYMAKER: frozenset({
        Permission.READ_AGGREGATES,
        Permission.READ_STATEWIDE,
        Permission.EXPORT_PDF,
    }),
    Role.AUDITOR: frozenset({
        Permission.READ_AUDIT,
        Permission.READ_AGGREGATES,
    }),
    Role.PLATFORM_ADMIN: frozenset({
        Permission.READ_AGGREGATES,
        Permission.READ_STATEWIDE,
        Permission.ADMIN_PIPELINE,
        Permission.READ_AUDIT,
    }),
}

#: Columns stripped from any response for roles without identity access.
IDENTITY_FIELDS = frozenset({
    "name", "ComplainantName", "AccusedName", "VictimName", "person_ref",
    "canonical_name", "left_name", "right_name", "io_name", "officer_name",
    "from_label", "to_label", "brief_facts", "brief_facts_kn", "BriefFacts",
})

#: Columns that require ``READ_SENSITIVE_DEMOGRAPHICS`` and are aggregate-only.
SENSITIVE_DEMOGRAPHIC_FIELDS = frozenset({"caste", "religion", "CasteID", "ReligionID", "caste_master_name",
                                          "ReligionName"})


@dataclass(slots=True)
class ScopeDescription:
    label: str
    unit_count: int
    statewide: bool


class AuthorizationService:
    def __init__(self, reference: ReferenceRepository) -> None:
        self._reference = reference

    # ---------------------------------------------------------------- scope
    def build_principal(self, account: Mapping[str, Any]) -> Principal:
        role = Role(account["role"])
        permissions = ROLE_PERMISSIONS[role]
        scope = self.scope_for(role, account.get("home_unit_id"), account.get("district_id"))
        return Principal(
            user_id=str(account["user_id"]),
            username=str(account["username"]),
            display_name=str(account["display_name"]),
            role=role,
            home_unit_id=account.get("home_unit_id"),
            district_id=account.get("district_id"),
            permissions=permissions,
            scope=scope,
        )

    def scope_for(self, role: Role, home_unit_id: int | None, district_id: int | None) -> UnitScope:
        if Permission.READ_STATEWIDE in ROLE_PERMISSIONS[role]:
            return UnitScope(root_unit_id=home_unit_id, statewide=True)
        unit_ids: set[int] = set()
        if home_unit_id is not None:
            unit_ids |= self._reference.descendant_unit_ids(int(home_unit_id))
        if district_id is not None:
            # An investigator posted to a district HQ sees that district's units.
            unit_ids |= self._reference.unit_ids_for_district(int(district_id)) if home_unit_id is None else set()
        return UnitScope(root_unit_id=home_unit_id, unit_ids=frozenset(unit_ids), statewide=False)

    def describe(self, principal: Principal) -> ScopeDescription:
        if principal.scope.statewide:
            return ScopeDescription(label="Karnataka (statewide)", unit_count=0, statewide=True)
        unit = self._reference.unit(principal.home_unit_id) if principal.home_unit_id else None
        label = unit["UnitName"] if unit else "no unit assigned"
        return ScopeDescription(
            label=f"{label} and subordinate units",
            unit_count=len(principal.scope.unit_ids),
            statewide=False,
        )

    # ----------------------------------------------------------- narrowing
    def narrow_scope(self, principal: Principal, unit_ids: Iterable[int] | None) -> UnitScope:
        """Intersect a request-level unit filter with the principal's scope."""
        requested = set(int(u) for u in (unit_ids or []))
        if not requested:
            return principal.scope
        if principal.scope.statewide:
            return UnitScope(root_unit_id=principal.scope.root_unit_id, unit_ids=frozenset(requested),
                             statewide=False)
        allowed = requested & set(principal.scope.unit_ids)
        if not allowed:
            raise AuthorizationError(
                "Requested units fall outside your authorized scope",
                requested=sorted(requested),
            )
        return UnitScope(root_unit_id=principal.scope.root_unit_id, unit_ids=frozenset(allowed), statewide=False)

    # -------------------------------------------------------- field masking
    def mask_row(self, principal: Principal, row: Mapping[str, Any]) -> dict[str, Any]:
        masked = dict(row)
        if not principal.has(Permission.READ_PERSON_IDENTITY):
            for field in IDENTITY_FIELDS & masked.keys():
                masked[field] = "[withheld: aggregate-only role]"
        if not principal.has(Permission.READ_SENSITIVE_DEMOGRAPHICS):
            for field in SENSITIVE_DEMOGRAPHIC_FIELDS & masked.keys():
                masked.pop(field, None)
        return masked

    def mask_rows(self, principal: Principal, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [self.mask_row(principal, row) for row in rows]

    def may_see_individuals(self, principal: Principal) -> bool:
        return principal.has(Permission.READ_PERSON_IDENTITY)

    def assert_aggregate_only_dimension(self, principal: Principal, dimension: str) -> None:
        """Caste/religion may drive aggregates only, never an individual lookup."""
        if dimension in {"caste", "religion"} and not principal.has(Permission.READ_SENSITIVE_DEMOGRAPHICS):
            raise AuthorizationError(
                "Caste and religion breakdowns require the sensitive-demographics permission",
                dimension=dimension,
                role=str(principal.role),
            )
