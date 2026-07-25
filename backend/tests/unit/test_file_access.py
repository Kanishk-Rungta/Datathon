"""File delivery must enforce ownership, not just authentication.

These are unit tests because the rule is a pure function of the key and the
principal — it should be impossible to regress without one of these failing.
"""

import pytest

from ksp_cip.application.services.authorization import ROLE_PERMISSIONS
from ksp_cip.domain.enums import Role
from ksp_cip.domain.errors import AuthorizationError
from ksp_cip.domain.models import Principal, UnitScope
from ksp_cip.interface.api.routers.files import authorize_file_access, resolve_owner

OWNER = "a" * 32
OTHER = "b" * 32


def person(role, user_id):
    return Principal(user_id=user_id, username="u", display_name="U", role=role,
                     permissions=frozenset(ROLE_PERMISSIONS[role]), scope=UnitScope(statewide=True))


class TestOwnerResolution:
    def test_export_keys_carry_their_owner(self):
        assert resolve_owner(f"exports/{OWNER}/session.pdf") == OWNER

    def test_unattributable_keys_have_no_owner(self):
        assert resolve_owner("landing/curated_CaseMaster/batch.ndjson") is None
        assert resolve_owner("random.pdf") is None


class TestOwnership:
    def test_a_user_may_read_their_own_export(self):
        principal = person(Role.INVESTIGATOR, OWNER)
        assert authorize_file_access(principal, f"exports/{OWNER}/s.pdf") == OWNER

    def test_a_user_may_not_read_another_users_export(self):
        principal = person(Role.INVESTIGATOR, OTHER)
        with pytest.raises(AuthorizationError):
            authorize_file_access(principal, f"exports/{OWNER}/s.pdf")

    def test_an_analyst_may_not_read_another_users_export(self):
        """Broad read permissions over data do not extend to other people's artefacts."""
        principal = person(Role.ANALYST, OTHER)
        with pytest.raises(AuthorizationError):
            authorize_file_access(principal, f"exports/{OWNER}/s.pdf")

    def test_an_auditor_may_read_any_export(self):
        principal = person(Role.AUDITOR, OTHER)
        assert authorize_file_access(principal, f"exports/{OWNER}/s.pdf") == OWNER


class TestRestrictedAreas:
    def test_the_landing_zone_is_admin_only(self):
        with pytest.raises(AuthorizationError):
            authorize_file_access(person(Role.ANALYST, OWNER), "landing/curated_CaseMaster/b.ndjson")

    def test_manifests_are_admin_only(self):
        with pytest.raises(AuthorizationError):
            authorize_file_access(person(Role.AUDITOR, OWNER), "manifests/seed_manifest.json")

    def test_an_admin_may_read_the_landing_zone(self):
        assert authorize_file_access(person(Role.PLATFORM_ADMIN, OWNER),
                                     "landing/curated_CaseMaster/b.ndjson") == "platform"


class TestPathSafety:
    def test_traversal_is_refused(self):
        with pytest.raises(AuthorizationError):
            authorize_file_access(person(Role.PLATFORM_ADMIN, OWNER), f"exports/{OWNER}/../../etc/passwd")

    def test_unattributable_keys_are_refused_even_for_privileged_roles(self):
        with pytest.raises(AuthorizationError):
            authorize_file_access(person(Role.ANALYST, OWNER), "stray-file.pdf")
