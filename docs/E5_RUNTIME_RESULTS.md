# E5 — Runtime and Result Contracts

E5A maps Basic v2 configuration into the existing E3/E4 recommendation
pipeline. E5B adds versioned recommendation, private run-manifest, and machine
run-result contracts around that same computation.

Recommendation JSON v1 preserves the explicit public candidate identity and
preprint metadata that the source supplied. Its score breakdown contains the
seven additive legacy components: similarity, recency, citations, altmetric,
journal quality, author bonus, and venue bonus. Every component reports its raw
value, weighted contribution, and whether its input was available. The
contributions reproduce the existing score without changing the formula,
weights, thresholds, or ordering.

Each v2 watch renders all requested output formats into a run-scoped staging
directory. After validation, the directory becomes an immutable generation and
`latest-success.json` atomically selects it. The pointer's finalized artifact
whitelist records generation-relative paths, SHA-256, byte size, media type, and
publishability. Stable files such as `feed.xml`, `report.html`, and
`recommendations.json` are compatibility aliases; downstream P2 code must use
the immutable whitelist returned by `RunResult`.

Private manifests live under the state root and contain only relative paths,
stable stage IDs, closed symbolic errors, and opaque diagnostic IDs. They do not
serialize credential values or names, raw Zotero user IDs/items, Custom base
URLs, absolute paths, exception strings, or tracebacks. `--machine-result`
emits one closed JSON object and uses exit categories 0, 2, 3, 4, or 5.

Candidate acquisition now exposes an additive outcome API. The legacy
`fetch_all()` list API remains available. Complete empty acquisition is a valid
result; all configured sources failing without a usable cache is a failed run;
partial or stale-cache acquisition is degraded. Strict v2 runs return exit 5
and do not replace the successful output pointer.

E5 does not add AI requests, change ranking, alter E3 synchronization or E4
state semantics, implement artifact transport, or change the public candidate
API. E0 fixtures and goldens remain unchanged.
