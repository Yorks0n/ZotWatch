# E4 computational state contract and release note

Status: implemented on `codex/v2-e4`, starting from `v2-e3-baseline`
(`8ceec503f81f701ead156c5695b3d115b1cac537`).

## Ranking invariant

Every profile, embedding matrix and FAISS index used by ranking belongs to one immutable,
validated generation. Its manifest must match the active SQLite mirror's pseudonymous library
identity fingerprint, committed Zotero revision and deterministic profile-input snapshot. It must
also match the local embedding model identity, dimension, input/normalization schemas, profile
configuration semantics, state compatibility version and profile builder ABI.

The complete engine version is recorded for audit. It is not a hard compatibility key: a patch
release can reuse state when all explicit ABI/schema/model/config fingerprints remain compatible.

## State layout and publication

```text
state/
  profile.sqlite
  cache/candidate_cache.json
  computational/
    current.json
    generations/<generation-id>/
      profile.json
      embeddings.npz
      faiss.index
      state-manifest.json
    staging/<run-id>.tmp/
  .zotwatch-state.lock
```

The builder reads identity, revision and ordered active items in one SQLite read transaction. It
computes every embedding for the generation, writes all artifacts under staging, reloads and
validates them, and rechecks the live SQLite snapshot before publication. A same-filesystem rename
makes the immutable generation visible; an fsynced temporary pointer then replaces `current.json`
atomically.

Failure before pointer replacement leaves the previous pointer intact. A renamed but unreferenced
generation is inert. A previous complete generation whose revision is behind SQLite remains useful
for diagnosis, but validation prevents it from reaching ranking.

## Model identity

Hard embedding compatibility uses stable descriptor fields: provider, requested model identifier,
resolved immutable upstream revision when locally available, stable artifact/config identity,
dimension, embedding input schema, normalization version and profile builder ABI.

The default local vectorizer resolves the standard Hugging Face cache `refs/main` and snapshot
directory without a network request. If no upstream revision is available, it uses the explicit
ZotWatch model contract identity. Raw float32 probe output is not a hard identity. The manifest may
carry a diagnostic probe fingerprint, but differences in that field do not invalidate a generation.

## Library identity and BUG-I4

`library_identity_sha256` is a pseudonymous fingerprint of the Zotero library type and ID. It does
not provide anonymity and the manifest never writes the raw Zotero user ID. Existing SQLite files
without this metadata, or state reused with another library, are not silently claimed: the official
CLI performs an E3 full sync and writes rows, identity and revision in the same atomic apply.

Embedding inputs use versioned structured fields (title, abstract, creators and tags). Content
changes clear the legacy SQLite embedding BLOB. E4 does not trust that BLOB for state reuse; every
new generation creates a complete `embeddings.npz`, profile and index from one matrix. This closes
BUG-I4 without changing Zotero transport semantics.

## Execution and lease ownership

The official coordinator acquires one non-reentrant cross-process lease per run:

```text
watch: acquire -> E3 sync -> ensure/build -> candidate fetch -> dedupe
       -> rank with the same StateHandle -> release

profile: acquire -> E3 sync -> ensure/build -> release
```

The lease is passed explicitly. Ingestor, StateManager, ProfileBuilder and WorkRanker validate or
consume it without nested acquisition. A standalone low-level StateManager or WorkRanker can own a
short lease for its operation.

`profile` reuses a fully compatible generation by default. `--full` and `--weekly` retain full
Zotero sync semantics and force a new generation. `watch` automatically rebuilds missing, stale or
corrupt state before loading top venues or ranking. A future manifest schema fails closed in normal
watch; an explicit full profile can build a current-engine generation while preserving the newer
directory.

## Intentional behavior changes

| ID | Change |
| --- | --- |
| E4-STATE-001 | Top-level unversioned profile/index files are no longer ranking authority. |
| E4-STATE-002 | `watch` rebuilds after a committed Zotero revision change, closing BUG-W1. |
| E4-STATE-003 | Content/model/dimension/profile-semantic changes invalidate the whole generation. |
| E4-STATE-004 | Missing, corrupt or mismatched artifacts are rejected before candidate search. |
| E4-STATE-005 | Interrupted or revision-drifted builds cannot replace the current pointer. |
| E4-STATE-006 | Missing/mismatched library identity requires a failure-safe full mirror sync. |
| E4-STATE-007 | Compatible default `profile` runs may reuse state; explicit full still rebuilds. |

Ranking weights, scoring functions, candidate public API behavior, E3 pagination/revision/trash
state machine, RSS/HTML payloads and E0 golden files are unchanged.

## Recovery and rollback

Missing/corrupt pointer, manifest, NPZ, profile or FAISS state is rebuildable. Delete
`computational/` and rerun `profile` if automatic recovery cannot proceed. A valid `profile.sqlite`
does not require Zotero reauthorization; deleting it triggers the normal E3 initial full sync.

Rolling code back to `v2-e3-baseline` leaves SQLite tables readable and ignores the added identity
metadata and `computational/` directory. Because E3 reads legacy top-level artifacts, run an E3
`profile --full` before an E3 `watch` after rollback. Do not pair a legacy index with a newer SQLite
revision.

## Validation evidence

The final local gate ran the E0-E4 suite twice without changing E0 fixtures or goldens:

- source checkout: `177 passed, 16 skipped`;
- forced wheel and editable-install probes: `193 passed`, with no skipped installation checks;
- `pip check`: no broken requirements in either installation;
- E0 fixture/golden diff from `v2-e0-baseline`: empty.
