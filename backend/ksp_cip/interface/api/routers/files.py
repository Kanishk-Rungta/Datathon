"""Authenticated file delivery for generated artefacts.

Authentication is not authorization. An export written for one officer must
not be readable by another simply because they also hold a valid token, so
this router enforces ownership from the key itself rather than trusting that
an unguessable path is protection enough.

Ownership is encoded in the key (``exports/<user_id>/...``) and checked here.
Oversight roles may read any artefact, and every such read is audited with the
owner recorded, because "an auditor opened someone else's export" is exactly
the kind of access an audit trail exists to capture.
"""

from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import Response

from ....domain.enums import Permission
from ....domain.errors import AuthorizationError, NotFoundError
from ....domain.models import Principal
from ..deps import ContainerDep, PrincipalDep

router = APIRouter(prefix="/files", tags=["files"])

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".json": "application/json",
    ".ndjson": "application/x-ndjson",
}

#: Keys of the form `exports/<user_id>/<name>` belong to that user.
OWNED_PREFIX_RE = re.compile(r"^(?P<area>exports|audio)/(?P<owner>[0-9a-fA-F]{16,64})/")

#: Areas no ordinary caller may read: the landing zone holds raw source
#: extracts and the manifests describe the entire dataset.
ADMIN_ONLY_AREAS = ("landing/", "manifests/")


def resolve_owner(file_path: str) -> str | None:
    match = OWNED_PREFIX_RE.match(file_path)
    return match.group("owner") if match else None


def authorize_file_access(principal: Principal, file_path: str) -> str:
    """Return the owner id, or raise. Oversight roles bypass ownership."""
    if ".." in file_path.split("/"):
        raise AuthorizationError("File path may not traverse directories", key=file_path)

    oversight = principal.has(Permission.READ_AUDIT) or principal.has(Permission.ADMIN_PIPELINE)

    if file_path.startswith(ADMIN_ONLY_AREAS):
        if not principal.has(Permission.ADMIN_PIPELINE):
            raise AuthorizationError(
                "This area holds source extracts and dataset manifests, and is restricted "
                "to platform administrators.",
                key=file_path, role=str(principal.role),
            )
        return "platform"

    owner = resolve_owner(file_path)
    if owner is None:
        # A key the platform cannot attribute is a key it cannot safely serve.
        raise AuthorizationError("This file is not attributable to an owner", key=file_path)

    if owner.lower() != principal.user_id.lower() and not oversight:
        raise AuthorizationError(
            "This artefact belongs to another user.",
            key=file_path, role=str(principal.role),
        )
    return owner


@router.get("/{file_path:path}")
def download(file_path: str, principal: PrincipalDep, container: ContainerDep) -> Response:
    owner = authorize_file_access(principal, file_path)

    if not container.filestore.exists(file_path):
        raise NotFoundError("No such file.", key=file_path)

    payload = container.filestore.read_bytes(file_path)
    suffix = file_path[file_path.rfind("."):] if "." in file_path else ""

    container.audit.record(
        action="file.download",
        principal=principal,
        object_type="file",
        object_ids=[file_path],
        outcome="success",
        detail={
            "owner": owner,
            "bytes": len(payload),
            "cross_user": owner.lower() not in (principal.user_id.lower(), "platform"),
        },
    )
    return Response(
        content=payload,
        media_type=CONTENT_TYPES.get(suffix, "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{file_path.rsplit("/", 1)[-1]}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
