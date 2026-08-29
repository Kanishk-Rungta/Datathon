"""The auth dependency must honour KSPCIP_IDENTITY_BACKEND, not bypass it.

``container.identity_provider`` is the component _build_identity() resolves
to per settings (the local service, or CatalystIdentityProvider). Calling
``container.identity_service`` directly from a route dependency silently
ignores an ``identity_backend=catalyst`` configuration: a Catalyst-issued
token would be checked against the local HS256 secret instead of the
Catalyst verification path in identity_provider, and would always fail (or
worse, succeed against the wrong secret if the two happened to match).
"""

from __future__ import annotations

from ksp_cip.interface.api.deps import get_principal


class _FakeIdentity:
    def __init__(self, principal, *, name):
        self.name = name
        self._principal = principal
        self.calls = 0

    def principal_from_token(self, token):
        self.calls += 1
        return self._principal


class _FakeContainer:
    def __init__(self, principal):
        self.identity_service = _FakeIdentity(principal, name="service")
        self.identity_provider = _FakeIdentity(principal, name="provider")


def test_get_principal_uses_the_configured_identity_provider():
    sentinel = object()
    container = _FakeContainer(sentinel)
    result = get_principal(container, authorization="Bearer sometoken")
    assert result is sentinel
    assert container.identity_provider.calls == 1
    assert container.identity_service.calls == 0
