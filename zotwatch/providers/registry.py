from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class Capability(str, Enum):
    GENERATION = "generation"
    NATIVE_RERANK = "native-rerank"


class AdapterStatus(str, Enum):
    IMPLEMENTED = "implemented"
    UNIMPLEMENTED = "unimplemented"


class StandardUIStatus(str, Enum):
    SELECTABLE = "selectable"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class CredentialDefinition:
    internal_id: str
    workflow_secret_name: str
    structured: bool = False
    required_fields: tuple[str, ...] = ("api_key",)


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    capabilities: frozenset[Capability]
    credential: CredentialDefinition
    recommended_models: tuple[str, ...] = ()
    registered: bool = True
    adapter_status: AdapterStatus = AdapterStatus.UNIMPLEMENTED
    standard_ui_status: StandardUIStatus = StandardUIStatus.HIDDEN


@dataclass(frozen=True)
class ProtocolDefinition:
    id: str
    capabilities: frozenset[Capability]
    registered: bool = True
    adapter_status: AdapterStatus = AdapterStatus.UNIMPLEMENTED
    standard_ui_status: StandardUIStatus = StandardUIStatus.HIDDEN


def _preset(provider_id: str, capability: Capability, secret_name: str) -> ProviderDefinition:
    return ProviderDefinition(
        id=provider_id,
        capabilities=frozenset({capability}),
        credential=CredentialDefinition(f"preset:{provider_id}", secret_name),
    )


_PROVIDERS = MappingProxyType(
    {
        definition.id: definition
        for definition in (
            _preset("voyage", Capability.NATIVE_RERANK, "VOYAGE_API_KEY"),
            _preset("dashscope", Capability.GENERATION, "DASHSCOPE_API_KEY"),
            _preset("openrouter", Capability.GENERATION, "OPENROUTER_API_KEY"),
            _preset("deepseek", Capability.GENERATION, "DEEPSEEK_API_KEY"),
            _preset("openai", Capability.GENERATION, "OPENAI_API_KEY"),
            _preset("anthropic", Capability.GENERATION, "ANTHROPIC_API_KEY"),
        )
    }
)

_PROTOCOLS = MappingProxyType(
    {
        definition.id: definition
        for definition in (
            ProtocolDefinition(
                "openai-compatible-generation", frozenset({Capability.GENERATION})
            ),
            ProtocolDefinition(
                "anthropic-compatible-generation", frozenset({Capability.GENERATION})
            ),
        )
    }
)

_CUSTOM_CONNECTIONS = MappingProxyType(
    {
        connection_id: CredentialDefinition(
            internal_id=f"custom:{connection_id}",
            workflow_secret_name=f"ZOTWATCH_CUSTOM_{index}_CREDENTIAL",
            structured=True,
            required_fields=("connection_id", "protocol", "base_url", "api_key"),
        )
        for index, connection_id in enumerate(
            ("custom-1", "custom-2", "custom-3", "custom-4"), start=1
        )
    }
)


def preset_provider_ids() -> tuple[str, ...]:
    return tuple(_PROVIDERS)


def protocol_ids() -> tuple[str, ...]:
    return tuple(_PROTOCOLS)


def custom_connection_ids() -> tuple[str, ...]:
    return tuple(_CUSTOM_CONNECTIONS)


def get_provider(provider_id: str) -> ProviderDefinition | None:
    return _PROVIDERS.get(provider_id)


def get_protocol(protocol_id: str) -> ProtocolDefinition | None:
    return _PROTOCOLS.get(protocol_id)


def get_custom_credential(connection_id: str) -> CredentialDefinition | None:
    return _CUSTOM_CONNECTIONS.get(connection_id)


def validate_registry() -> None:
    definitions = (*_PROVIDERS.values(), *_PROTOCOLS.values())
    if any(not item.registered for item in definitions):
        raise RuntimeError("registry entries must be registered")
    if any(
        item.standard_ui_status is StandardUIStatus.SELECTABLE
        and item.adapter_status is not AdapterStatus.IMPLEMENTED
        for item in definitions
    ):
        raise RuntimeError("standard UI cannot expose an unimplemented adapter")
    credentials = [item.credential for item in _PROVIDERS.values()]
    credentials.extend(_CUSTOM_CONNECTIONS.values())
    if len({item.internal_id for item in credentials}) != len(credentials):
        raise RuntimeError("credential internal IDs must be unique")
    if len({item.workflow_secret_name for item in credentials}) != len(credentials):
        raise RuntimeError("credential workflow slots must be unique")


validate_registry()


__all__ = [
    "AdapterStatus",
    "Capability",
    "CredentialDefinition",
    "ProviderDefinition",
    "ProtocolDefinition",
    "StandardUIStatus",
    "custom_connection_ids",
    "get_custom_credential",
    "get_protocol",
    "get_provider",
    "preset_provider_ids",
    "protocol_ids",
    "validate_registry",
]
