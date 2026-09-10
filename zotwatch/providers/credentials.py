from __future__ import annotations

from .registry import CredentialDefinition, get_custom_credential, get_provider


def credential_for_provider(provider_id: str) -> CredentialDefinition | None:
    provider = get_provider(provider_id)
    return provider.credential if provider else None


def credential_for_custom_connection(connection_id: str) -> CredentialDefinition | None:
    return get_custom_credential(connection_id)


__all__ = ["credential_for_custom_connection", "credential_for_provider"]
