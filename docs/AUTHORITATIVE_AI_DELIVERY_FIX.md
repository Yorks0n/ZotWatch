# Authoritative AI delivery (2026-10-27)

The daily engine selects diverse candidates before enforcing the preprint cap.
Repeating selection on the admitted pool changes order and can change membership.
AI delivery now uses the validated final recommendation list directly.

Diagnostic recall decisions and generation identity are used only to authorize
private quality context. Missing, stale or inconsistent auxiliary evidence skips
quality assessment while public translation continues. Foreign workspace caches
and unrelated run diagnostics are ignored. The authoritative result schema,
unique work identities and public sidecar binding remain enforced.

Missing abstracts still use exact DOI metadata recovery. A paper without a verified
abstract retains its original text and does not consume another paper's budget.
Profile/vector or quality failures retain recommendations and allow translation.
Successful API output survives cache and diagnostic write failures.

Operational summaries include fixed warning codes. Unavoidable whole-delivery
fallbacks also report exception type and code location, without exception text,
credentials or private context. GitHub Actions receives warning annotations.

Regression coverage includes more than 20 admitted candidates with preprint
removal, changed final membership under repeated selection, auxiliary evidence
failures, foreign scopes, invalid authoritative identity, optional quality errors,
unwritable caches and diagnostics, and missing abstracts in published RSS.
