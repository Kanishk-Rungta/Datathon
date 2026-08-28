"""Authentication and session identity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ....domain.enums import Permission
from ..deps import ContainerDep, PrincipalDep
from ..schemas import LoginRequest, LoginResponse, PrincipalResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _principal_response(container: ContainerDep, principal: Any) -> PrincipalResponse:
    description = container.authorization.describe(principal)
    return PrincipalResponse(
        user_id=principal.user_id,
        username=principal.username,
        display_name=principal.display_name,
        role=str(principal.role),
        permissions=sorted(str(p) for p in principal.permissions),
        home_unit_id=principal.home_unit_id,
        district_id=principal.district_id,
        scope_summary=description.label,
        scope_unit_count=description.unit_count,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, container: ContainerDep) -> LoginResponse:
    principal, token = container.identity_service.authenticate(payload.username, payload.password)
    container.audit.record(
        action="auth.login", principal=principal, object_type="user",
        object_ids=[principal.user_id], outcome="success",
    )
    return LoginResponse(
        access_token=token["access_token"],
        expires_in=token["expires_in"],
        principal=_principal_response(container, principal),
    )


@router.get("/me", response_model=PrincipalResponse)
def me(principal: PrincipalDep, container: ContainerDep) -> PrincipalResponse:
    return _principal_response(container, principal)


@router.get("/demo-accounts")
def demo_accounts(container: ContainerDep) -> dict[str, Any]:
    """Role list for the demo, shown only in synthetic-data environments.

    Listed in ``local`` and ``development`` — both run entirely on synthetic
    seed data, and a reviewer opening a Development deployment cannot sign in
    without knowing the accounts. Withheld in ``staging`` and ``production``,
    where the data may be real and the seeded demo accounts are disabled
    anyway.
    """
    from ....config.settings import Environment

    DEMO_ENVIRONMENTS = {Environment.LOCAL, Environment.DEVELOPMENT}
    if container.settings.environment not in DEMO_ENVIRONMENTS:
        return {"accounts": [], "note": "Demo accounts are not listed in this environment."}
    from ....application.pipeline import DEMO_PASSWORD, DEMO_USERS

    return {
        "password": DEMO_PASSWORD,
        "accounts": [
            {"username": username, "display_name": display, "role": str(role)}
            for username, display, role, _district in DEMO_USERS
        ],
        "note": "Synthetic demo credentials. These accounts exist only where the data is synthetic.",
    }
