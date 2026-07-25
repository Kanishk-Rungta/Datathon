"""Role permissions and field masking, without touching a database."""

import pytest

from ksp_cip.application.services.authorization import ROLE_PERMISSIONS, AuthorizationService
from ksp_cip.domain.enums import Permission, Role
from ksp_cip.domain.errors import AuthorizationError
from ksp_cip.domain.models import Principal, UnitScope


class StubReference:
    def descendant_unit_ids(self, unit_id):
        return {unit_id, unit_id + 1, unit_id + 2}

    def unit_ids_for_district(self, district_id):
        return {9001, 9002}


@pytest.fixture
def service():
    return AuthorizationService(StubReference())


def principal(role, **kwargs):
    return Principal(
        user_id="u", username="u", display_name="U", role=role,
        permissions=frozenset(ROLE_PERMISSIONS[role]),
        scope=kwargs.pop("scope", UnitScope(statewide=True)), **kwargs,
    )


class TestRoleMatrix:
    def test_every_role_has_a_permission_set(self):
        assert set(ROLE_PERMISSIONS) == set(Role)

    def test_investigator_is_not_statewide(self):
        assert Permission.READ_STATEWIDE not in ROLE_PERMISSIONS[Role.INVESTIGATOR]

    def test_audit_log_is_restricted_to_oversight_roles(self):
        readers = {role for role, perms in ROLE_PERMISSIONS.items() if Permission.READ_AUDIT in perms}
        assert readers == {Role.SUPERVISOR, Role.AUDITOR, Role.PLATFORM_ADMIN}
        assert Role.INVESTIGATOR not in readers
        assert Role.ANALYST not in readers

    def test_policymaker_cannot_read_case_detail(self):
        """A policy adviser needs aggregates, not named individuals."""
        assert Permission.READ_CASE_DETAIL not in ROLE_PERMISSIONS[Role.POLICYMAKER]
        assert Permission.READ_AGGREGATES in ROLE_PERMISSIONS[Role.POLICYMAKER]

    def test_only_pipeline_admin_can_run_the_pipeline(self):
        admins = {role for role, perms in ROLE_PERMISSIONS.items() if Permission.ADMIN_PIPELINE in perms}
        assert admins == {Role.PLATFORM_ADMIN}


class TestScope:
    def test_statewide_roles_get_a_statewide_scope(self, service):
        scope = service.scope_for(Role.ANALYST, home_unit_id=100, district_id=1)
        assert scope.statewide is True

    def test_investigator_scope_is_the_unit_subtree(self, service):
        scope = service.scope_for(Role.INVESTIGATOR, home_unit_id=100, district_id=1)
        assert scope.statewide is False
        assert scope.unit_ids == frozenset({100, 101, 102})

    def test_scope_allows_only_units_inside_it(self, service):
        scope = service.scope_for(Role.INVESTIGATOR, home_unit_id=100, district_id=None)
        assert scope.allows(101)
        assert not scope.allows(500)
        assert not scope.allows(None)


class TestPermissionChecks:
    def test_require_raises_for_a_missing_permission(self):
        with pytest.raises(AuthorizationError):
            principal(Role.POLICYMAKER).require(Permission.READ_CASE_DETAIL)

    def test_require_passes_for_a_held_permission(self):
        principal(Role.ANALYST).require(Permission.READ_AGGREGATES)


class TestMasking:
    def test_identity_fields_are_masked_without_permission(self, service):
        row = {"AccusedName": "Ramesh Gowda", "CrimeNo": "104430006202600001", "AgeYear": 30}
        masked = service.mask_row(principal(Role.POLICYMAKER), dict(row))
        assert masked["AccusedName"] != "Ramesh Gowda"
        assert masked["CrimeNo"] == row["CrimeNo"], "case references stay verifiable"

    def test_identity_fields_survive_with_permission(self, service):
        row = {"AccusedName": "Ramesh Gowda"}
        masked = service.mask_row(principal(Role.INVESTIGATOR), dict(row))
        assert masked["AccusedName"] == "Ramesh Gowda"

    def test_sensitive_demographics_are_dropped_without_permission(self, service):
        """Caste and religion are removed outright rather than blanked, so a
        caller cannot infer their presence from a masked placeholder."""
        row = {"CasteID": 3, "ReligionID": 2, "ComplainantName": "A"}
        masked = service.mask_row(principal(Role.INVESTIGATOR), dict(row))
        assert "CasteID" not in masked
        assert "ReligionID" not in masked
        assert masked["ComplainantName"] == "A"

    def test_aggregate_only_dimension_is_refused_for_row_level_use(self, service):
        with pytest.raises(AuthorizationError):
            service.assert_aggregate_only_dimension(principal(Role.INVESTIGATOR), "caste")

    def test_permitted_dimension_passes(self, service):
        service.assert_aggregate_only_dimension(principal(Role.ANALYST), "occupation")
