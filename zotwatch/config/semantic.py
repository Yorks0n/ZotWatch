from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
from types import MappingProxyType
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from zotwatch.providers import (
    AdapterStatus,
    Capability,
    StandardUIStatus,
    credential_for_custom_connection,
    get_protocol,
    get_provider,
)

from .errors import ConfigError
from .models import CustomServiceConfig, PresetServiceConfig, ZotWatchConfigV2


_FEATURE_CAPABILITIES = MappingProxyType(
    {
        "rerank": frozenset({Capability.GENERATION, Capability.NATIVE_RERANK}),
        "summary": frozenset({Capability.GENERATION}),
    }
)
_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal"})


@dataclass(frozen=True)
class ResolvedFeatureRoute:
    feature: str
    service_id: str
    provider_id: str
    protocol_id: str | None
    capabilities: frozenset[Capability]
    credential_internal_id: str
    adapter_status: AdapterStatus
    standard_ui_status: StandardUIStatus

    @property
    def runtime_available(self) -> bool:
        return self.adapter_status is AdapterStatus.IMPLEMENTED


@dataclass(frozen=True)
class _CustomBinding:
    protocol: str
    normalized_base_url: str = field(repr=False)


def _error(code: str, pointer: str, message: str) -> ConfigError:
    return ConfigError(code, message, json_pointer=pointer)


def _validate_model_identifier(model: str, pointer: str) -> None:
    if any(unicodedata.category(character).startswith("C") for character in model):
        raise _error("CONFIG_SCHEMA", pointer, "model identifier contains control characters")


def normalize_custom_base_url(value: str, pointer: str = "/") -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise _error("CUSTOM_URL_INVALID", pointer, "Custom base URL is invalid") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise _error("CUSTOM_URL_INVALID", pointer, "Custom base URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise _error("CUSTOM_URL_INVALID", pointer, "Custom base URL cannot contain userinfo")
    if parsed.query or parsed.fragment:
        raise _error("CUSTOM_URL_INVALID", pointer, "Custom base URL cannot contain query or fragment")
    hostname = parsed.hostname.rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _error("CUSTOM_URL_INVALID", pointer, "Custom base URL hostname is invalid") from exc
    if hostname in _BLOCKED_HOSTS or hostname.endswith((".localhost", ".local")):
        raise _error("CUSTOM_URL_INVALID", pointer, "Custom base URL target is not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise _error("CUSTOM_URL_INVALID", pointer, "Custom base URL target is not allowed")
    rendered_host = f"[{hostname}]" if address and address.version == 6 else hostname
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def resolve_feature_routes(config: ZotWatchConfigV2) -> tuple[ResolvedFeatureRoute, ...]:
    custom_bindings: dict[str, _CustomBinding] = {}
    service_metadata: dict[str, tuple[str, str | None, frozenset[Capability], str,
                                     AdapterStatus, StandardUIStatus]] = {}

    for service_id, service in config.ai.services.items():
        pointer = f"/ai/services/{service_id}"
        _validate_model_identifier(service.model, f"{pointer}/model")
        if isinstance(service, CustomServiceConfig):
            protocol = get_protocol(service.protocol)
            if protocol is None:
                raise _error("UNKNOWN_PROTOCOL", f"{pointer}/protocol", "Custom protocol is not registered")
            credential = credential_for_custom_connection(service.connection_id)
            if credential is None:
                raise _error(
                    "CUSTOM_CONNECTION_UNKNOWN",
                    f"{pointer}/connection_id",
                    "Custom connection is not registered",
                )
            normalized = normalize_custom_base_url(service.base_url, f"{pointer}/base_url")
            binding = _CustomBinding(service.protocol, normalized)
            previous = custom_bindings.setdefault(service.connection_id, binding)
            if previous != binding:
                raise _error(
                    "CUSTOM_CONNECTION_CONFLICT",
                    f"{pointer}/connection_id",
                    "Custom connection has conflicting protocol or endpoint bindings",
                )
            service_metadata[service_id] = (
                "custom",
                service.protocol,
                protocol.capabilities,
                credential.internal_id,
                protocol.adapter_status,
                protocol.standard_ui_status,
            )
        elif isinstance(service, PresetServiceConfig):
            provider = get_provider(service.provider)
            if provider is None:
                raise _error("UNKNOWN_PROVIDER", f"{pointer}/provider", "Preset provider is not registered")
            service_metadata[service_id] = (
                provider.id,
                None,
                provider.capabilities,
                provider.credential.internal_id,
                provider.adapter_status,
                provider.standard_ui_status,
            )

    routes: list[ResolvedFeatureRoute] = []
    for feature in ("rerank", "summary"):
        route = getattr(config.ai.features, feature)
        if not route.enabled:
            continue
        assert route.service is not None
        metadata = service_metadata.get(route.service)
        if metadata is None:
            raise _error(
                "UNKNOWN_SERVICE",
                f"/ai/features/{feature}/service",
                "Feature references an unknown service",
            )
        provider_id, protocol_id, capabilities, credential_id, adapter_status, ui_status = metadata
        if not capabilities.intersection(_FEATURE_CAPABILITIES[feature]):
            raise _error(
                "CAPABILITY_MISMATCH",
                f"/ai/features/{feature}/service",
                "Service does not provide the capability required by this feature",
            )
        routes.append(
            ResolvedFeatureRoute(
                feature=feature,
                service_id=route.service,
                provider_id=provider_id,
                protocol_id=protocol_id,
                capabilities=capabilities,
                credential_internal_id=credential_id,
                adapter_status=adapter_status,
                standard_ui_status=ui_status,
            )
        )
    return tuple(routes)


__all__ = ["ResolvedFeatureRoute", "normalize_custom_base_url", "resolve_feature_routes"]
