"""Read-only provider and credential registry APIs."""

from .credentials import credential_for_custom_connection, credential_for_provider
from .registry import (
    AdapterStatus,
    Capability,
    ProviderDefinition,
    ProtocolDefinition,
    StandardUIStatus,
    custom_connection_ids,
    get_provider,
    get_protocol,
    preset_provider_ids,
    protocol_ids,
)

__all__ = [
    "AdapterStatus",
    "Capability",
    "ProviderDefinition",
    "ProtocolDefinition",
    "StandardUIStatus",
    "credential_for_custom_connection",
    "credential_for_provider",
    "custom_connection_ids",
    "get_provider",
    "get_protocol",
    "preset_provider_ids",
    "protocol_ids",
]
