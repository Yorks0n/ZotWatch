# E5A — Basic v2 Runtime Bridge

E5A maps a validated Basic v2 `zotwatch.yaml` into the existing E3 sync, E4
computational-state, candidate, dedupe, ranking, RSS, and HTML pipeline. Legacy
configuration continues through its existing entry point. The two configuration
sources are detected explicitly and are never merged.

The runtime preflight completes before Zotero synchronization, model loading,
candidate networking, or computational-state generation. It separates whether a
fixed credential requirement is configured, whether the selected capability has
an implemented runtime adapter, and whether an optional upstream verification was
requested. Its diagnostics contain stable requirement IDs and do not expose
credential values, environment names, GitHub Secret names, Custom base URLs, or
complete service records.

At the E5A checkpoint, RSS and HTML are executable output formats. A v2 request
for JSON fails with `OUTPUT_FORMAT_UNAVAILABLE` and exit category 3 during
preflight. E5B owns the versioned JSON result contract and will enable that
capability.

Basic v2 uses the single legacy-v1 scoring policy constant and the bundled
journal metrics resource. Regression coverage compares the complete ranked
objects and RSS/HTML bytes for equivalent default legacy and Basic v2 runs. It
does not change sync, state, candidate fetching, dedupe, scoring formulas,
filters, ordering, or writer behavior.

The E5A gate includes source, wheel, editable-install, config, characterization,
and installation probes. E0 fixtures and goldens remain byte-for-byte unchanged.
