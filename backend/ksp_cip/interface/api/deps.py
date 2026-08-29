"""FastAPI dependencies: container access, authentication, correlation IDs."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, Request

from ...domain.enums import Permission
from ...domain.errors import AuthenticationError, AuthorizationError
from ...domain.models import Principal
from ..container import Container


def get_container_from_request(request: Request) -> Container:
    container: Container | None = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - app factory always sets this
        raise RuntimeError("Application container is not configured")
    return container


ContainerDep = Annotated[Container, Depends(get_container_from_request)]


def get_principal(
    container: ContainerDep,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("A bearer token is required for this endpoint")
    token = authorization.split(" ", 1)[1].strip()
    return container.identity_provider.principal_from_token(token)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require(permission: Permission):
    """Dependency factory enforcing a single permission."""

    def dependency(principal: PrincipalDep) -> Principal:
        if not principal.has(permission):
            raise AuthorizationError(
                f"Your role does not include '{permission}'.",
                role=str(principal.role), permission=str(permission),
            )
        return principal

    return dependency


def scope_note(principal: Principal) -> str:
    """A one-line description of what the caller can see, attached to results."""
    if principal.scope.statewide:
        return "Results cover all police units in the state."
    return (
        f"Results are limited to the {len(principal.scope.unit_ids)} police unit(s) "
        "within your authorized command."
    )
