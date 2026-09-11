from __future__ import annotations

from uuid import uuid4

from .models import RunError


ERROR_MESSAGES = {
    "CONFIG_INVALID": "Configuration could not be validated.",
    "CONFIG_MIXED_MODES": "Legacy and v2 configuration cannot be used together.",
    "CONFIG_OPTION_UNSUPPORTED": "This command option is unavailable for the selected configuration.",
    "OUTPUT_FORMAT_UNAVAILABLE": "A requested output format is unavailable in this engine revision.",
    "CAPABILITY_UNAVAILABLE": "A configured capability has no executable runtime adapter.",
    "CREDENTIAL_MISSING": "A required credential is not configured.",
    "CREDENTIAL_MALFORMED": "A required credential has an invalid shape.",
    "CREDENTIAL_VERIFICATION_FAILED": "Explicit credential verification failed.",
    "ZOTERO_SYNC_FAILED": "Zotero synchronization failed.",
    "STATE_INCOMPATIBLE": "The computational state is incompatible.",
    "STATE_BUILD_FAILED": "The computational state could not be built.",
    "STATE_CORRUPT": "The computational state is corrupt.",
    "CANDIDATE_PARTIAL": "Some candidate sources were unavailable.",
    "CANDIDATE_STALE_CACHE": "Candidate acquisition used a stale cached result.",
    "CANDIDATE_UNAVAILABLE": "Candidate acquisition was unavailable.",
    "CANDIDATE_PAYLOAD_INVALID": "A candidate response did not satisfy its contract.",
    "DEDUPE_FAILED": "Candidate deduplication failed.",
    "RANKING_FAILED": "Candidate ranking failed.",
    "OUTPUT_CONTRACT_INVALID": "Recommendation output did not satisfy its contract.",
    "OUTPUT_RENDER_FAILED": "Recommendation output could not be rendered.",
    "OUTPUT_PUBLISH_FAILED": "Recommendation output could not be published.",
    "ZOTERO_WRITEBACK_FAILED": "Zotero write-back failed.",
    "INTERNAL_ERROR": "The run failed at an internal boundary.",
}


def run_error(code: str) -> RunError:
    safe_code = code if code in ERROR_MESSAGES else "INTERNAL_ERROR"
    return RunError(
        code=safe_code,
        message=ERROR_MESSAGES[safe_code],
        diagnostic_id=uuid4().hex,
    )


__all__ = ["ERROR_MESSAGES", "run_error"]
