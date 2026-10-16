from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict
import requests

from zotwatch.config import ConfigSource
from zotwatch.providers import AdapterStatus, credential_for_custom_connection, credential_for_provider

from .config import EffectiveRuntimeConfig


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequirementStatus(_Model):
    requirement_id: str
    configured: bool
    runtime_supported: bool
    verification: Literal["not_requested", "verified", "failed", "unsupported"]
    user_credential_required: bool = True


class PreflightReport(_Model):
    schema_version: Literal[1] = 1
    ready: bool
    error_code: str | None
    requirements: tuple[RequirementStatus, ...]


def _slot_for_route(route) -> str | None:
    if route.provider_id == "custom":
        connection_id = route.credential_internal_id.removeprefix("custom:")
        credential = credential_for_custom_connection(connection_id)
    else:
        credential = credential_for_provider(route.provider_id)
    return credential.workflow_secret_name if credential else None


def preflight(
    config: EffectiveRuntimeConfig,
    *,
    verify_zotero: bool = False,
    session: requests.Session | None = None,
) -> PreflightReport:
    settings = config.settings
    identity_present = bool(settings.zotero.api.user_id) and not settings.zotero.api.user_id.startswith("${")
    key_slot = settings.zotero.api.api_key_env
    key_present = bool(os.getenv(key_slot))
    verification: Literal["not_requested", "verified", "failed", "unsupported"] = "not_requested"

    error_code = None
    if any(route.adapter_status is not AdapterStatus.IMPLEMENTED for route in config.feature_routes):
        error_code = "CAPABILITY_UNAVAILABLE"
    elif not identity_present or not key_present:
        error_code = "CREDENTIAL_MISSING"

    if verify_zotero and error_code is None:
        client = session or requests.Session()
        try:
            response = client.get(
                f"https://api.zotero.org/users/{settings.zotero.api.user_id}/items",
                params={"limit": 1},
                headers={"Zotero-API-Key": os.environ[key_slot]},
                timeout=15,
            )
            response.raise_for_status()
            verification = "verified"
        except requests.RequestException:
            verification = "failed"
            error_code = "CREDENTIAL_VERIFICATION_FAILED"

    requirements = [
        RequirementStatus(
            requirement_id="zotero.identity",
            configured=identity_present,
            runtime_supported=True,
            verification=verification,
        ),
        RequirementStatus(
            requirement_id="zotero.read",
            configured=key_present,
            runtime_supported=True,
            verification=verification,
        ),
    ]
    if config.settings.sources.public_api.enabled:
        requirements.append(
            RequirementStatus(
                requirement_id="public-candidates",
                configured=True,
                runtime_supported=True,
                verification="not_requested",
                user_credential_required=False,
            )
        )
    for route in config.feature_routes:
        slot = _slot_for_route(route)
        requirements.append(
            RequirementStatus(
                requirement_id=f"feature.{route.feature}",
                configured=bool(slot and os.getenv(slot)),
                runtime_supported=route.adapter_status is AdapterStatus.IMPLEMENTED,
                verification="unsupported"
                if route.adapter_status is not AdapterStatus.IMPLEMENTED
                else "not_requested",
            )
        )
    return PreflightReport(
        ready=error_code is None,
        error_code=error_code,
        requirements=tuple(requirements),
    )


__all__ = ["PreflightReport", "RequirementStatus", "preflight"]
