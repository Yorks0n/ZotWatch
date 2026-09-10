# E3 Implementation Plan — Zotero Sync Correctness

Status: **plan only; awaiting review**  
Branch: `codex/v2-e3`  
Starting baseline: `2c276da8696618fcd4acd4b34eaf8b1b900219a4` (`v2-e2-baseline`)  
Scope: ZotWatch repository only. This document does not modify production behavior.

## 1. Goal and correctness invariant

E3 makes the personal SQLite Zotero mirror correspond to one explicitly committed Zotero library revision. A successful sync has this invariant:

> Every item change and deletion from the previously committed revision through the target revision has been observed, validated, and applied in one SQLite transaction; the persisted watermark names exactly that target revision.

A failed sync leaves both the item rows and persisted watermark at their pre-run values. Full sync establishes the mirror from a complete remote snapshot. Incremental sync applies every change since the last successful revision. E3 does not attempt distributed exactly-once delivery: stable Zotero revisions, idempotent staging, and one local transaction provide the required guarantee.

## 2. Current state machine and data flow

Current production flow in `src/ingest_zotero_api.py` is:

1. Initialize SQLite.
2. Read `metadata.last_modified_version`, unless `full=True`, which sets the request watermark to `None`.
3. Fetch `/users/{id}/items` page by page. Only the first page receives `If-Modified-Since-Version`; incremental requests do not send a `since` query parameter.
4. Parse and immediately call `ProfileStorage.upsert_item()` for each item. Every upsert commits independently.
5. Track the maximum `Last-Modified-Version` seen. A missing header currently becomes `0`.
6. Catch `requests.RequestException` for item pagination, log it, and continue after any already committed partial writes.
7. Request `/deleted` with the maximum revision just observed, rather than the revision committed before the run. A deletion request failure is also logged and ignored.
8. Delete rows in another immediate commit.
9. If any item was fetched, or the run is full, write the maximum revision in a final independent commit.

`src/storage.py` exposes separately committing item, deletion, and metadata methods. There is no sync transaction or remote snapshot reconciliation. The existing metadata key `last_modified_version` is the only durable sync watermark.

## 3. Confirmed defects and triggers

E3 assigns stable regression identifiers to each intentional behavior change.

| ID | Trigger | Current result | Required result |
| --- | --- | --- | --- |
| E3-SYNC-001 | A later item page fails after an earlier page was written | Earlier rows remain committed and the new watermark can still be committed | No item or watermark change; the run fails |
| E3-SYNC-002 | Items response advances from `R0` to `Rt`, with tombstones after `R0` | `/deleted?since=Rt` can skip deletions in `(R0, Rt]` | Query deletions with the immutable starting revision `R0` |
| E3-SYNC-003 | Full sync returns a set missing a local row | Full mode only upserts, leaving the stale row | Atomically reconcile local keys to the complete remote key set |
| E3-SYNC-004 | Deleted endpoint fails after item pages succeeded | Item writes remain and deletion is skipped while the watermark may advance | Abort without any SQLite change |
| E3-SYNC-005 | Zotero's library revision changes between pages or between items and deleted responses | Pages from different revisions can be combined and their maximum committed | Discard the staged attempt and restart; never combine revisions |
| E3-SYNC-006 | A response lacks a valid `Last-Modified-Version`, has an invalid payload, or contains an invalid record | Missing revision can be interpreted as `0`; partial earlier records may already be committed | Treat the attempt as incomplete and leave SQLite unchanged |
| E3-SYNC-007 | Incremental mode has a committed watermark | Conditional request is used, but no `since=R0` item query is sent | Request the delta since `R0`, while retaining the first-request no-op conditional |
| E3-SYNC-008 | A page or item is replayed | It is counted and upserted repeatedly | Stage by item key; replay is idempotent and cannot regress the final object |
| E3-SYNC-009 | An item moves to trash or is restored | Default `/items` excludes trash, so a trash transition can leave a stale local row while the watermark advances | Use `includeTrashed=1` as the transport domain and project `data.deleted` into the non-trash local mirror domain |

Existing E0 observation labels map as follows: BUG-I1 → E3-SYNC-002, BUG-I2 → E3-SYNC-001/004, and BUG-I3 → E3-SYNC-003. BUG-I4, stale embedding retention after content change, belongs to computational state work and remains outside E3. The E3 release note will identify the changed cases instead of rewriting unrelated characterization output.

## 4. Proposed state machine

Each run separates remote acquisition from local commit.

```text
READ_COMMITTED
  read R_start from SQLite
  choose FULL when --full or R_start is absent; otherwise INCREMENTAL
        |
        v
FETCH_ITEMS
  fetch and validate pages into memory
  establish R_target from first 200 response
  require every page revision == R_target
        |
        +-- 304 in incremental mode --> NO_OP(R_start)
        |
        v
FETCH_DELETIONS (incremental only)
  GET /deleted?since=R_start
  require response revision == R_target
        |
        v
VALIDATE_COMPLETE
  validate payloads, item keys/versions, links, duplicates, tombstones
        |
        +-- revision drift --> discard and retry bounded attempt
        +-- any other failure --> fail, SQLite unchanged
        |
        v
COMMIT_SQLITE
  BEGIN IMMEDIATE
  apply upserts
  apply tombstones, or full snapshot reconciliation
  write last_modified_version = R_target
  COMMIT
        |
        +-- SQLite error --> ROLLBACK
        v
SUCCESS(R_target)
```

Network access never occurs inside the SQLite transaction. A run does not call the current individually committing storage methods while acquiring pages.

### 4.1 Revision names

- `R_start`: `metadata.last_modified_version` read before the attempt. It is the last successful committed library revision and remains immutable throughout all retries in one `run()` call.
- `R_target`: the `Last-Modified-Version` from the first successful items response. It is the remote snapshot/delta revision the attempt intends to commit.
- `R_observed`: the last validated response revision during acquisition. It may only equal `R_target`; a different value is revision drift, not a new maximum to absorb.
- `R_commit`: the revision written with the item changes. It is assigned only inside the successful SQLite transaction and must equal `R_target`. On failure, durable `R_commit` remains `R_start`.

`IngestStats` will expose these distinctions using optional `start_revision`, `target_revision`, `observed_revision`, and `committed_revision` fields. The existing `last_modified_version` field remains as a compatibility alias/value for the successful committed revision. It is never populated with an uncommitted observation.

The legacy counter fields retain their observable meaning wherever it already exists:

- `fetched` counts valid item records observed from the transport, including replayed pages and, after E3, explicit trash-state records;
- `updated` counts valid non-trashed item records accepted for local upsert, including unchanged and replayed records, so historical no-trash runs continue to report one `updated` per fetched item;
- `removed` counts unique removal intents produced by permanent tombstones, trash transitions, and full-snapshot stale-key reconciliation.

Atomic apply also returns new `applied_inserted`, `applied_changed`, and `applied_removed` counters for actual SQLite row mutations. A replay or unchanged item can therefore increment legacy `updated` while leaving `applied_changed` at zero. An unknown tombstone can increment legacy `removed` while leaving `applied_removed` at zero. These semantics will have dedicated regressions and release-note documentation; E3 does not silently redefine `updated` to mean actual changed rows.

### 4.2 Stable response requirement

Every 200 item page and the incremental deleted response must contain a parseable non-negative `Last-Modified-Version`. All values in one attempt must equal `R_target`. A higher or lower value means the library changed or the response set is inconsistent; the attempt is discarded.

Revision drift receives a small bounded whole-attempt retry using the same `R_start`. The exact limit will be a named internal constant and covered by tests. Ordinary HTTP retry remains in `request_with_retry`; after that layer is exhausted, item/deletion failures propagate as a typed sync failure rather than multiplying requests with another whole-run network retry. A later CLI or workflow run starts again from the unchanged watermark.

### 4.3 Incremental no-op

The first incremental item request sends both the delta query `since=R_start` and `If-Modified-Since-Version: R_start`. A 304 means the library has not changed since the committed revision, so the run returns success at `R_start` without querying deletions or writing SQLite. This interpretation treats the conditional as library-version scoped, not item-list scoped.

## 5. Full and incremental semantics

### 5.1 Transport domain and local mirror domain

Zotero's official API documentation states that `/items` excludes trashed items by default, while the official sync sequence requests item changes with `includeTrashed=1`. Zotero Data Server API v3 serializes a trashed item directly as `data.deleted: 1`; after restore the `deleted` property is absent. E3 uses this field only: present `1`/`true` means trashed, absence means active, and any other present value is malformed. It does not infer trash from title, item type, endpoint source, or missing membership.

References used to fix the contract:

- [Zotero Web API v3 syncing sequence](https://www.zotero.org/support/dev/web_api/v3/syncing)
- [Zotero Web API item endpoint and `includeTrashed` semantics](https://www.zotero.org/support/dev/web_api/v3/basics)
- [Official Data Server API v3 include-trash and restore tests](https://github.com/zotero/dataserver/blob/476ed12c18cbd431170346702882d091710f5f61/tests/remote/tests/3/item.test.js#L2089-L2215)

This creates two explicit domains:

- **Synchronization transport domain:** both full and incremental `/items` requests use `includeTrashed=1`, so trash and restore are observable versioned item changes.
- **ZotWatch local mirror domain:** only active items are stored and used by profile building. Trashed items are staged as removal intents, never projected into the local item table.

In an incremental delta, `data.deleted: 1` removes the local row and an active response for a previously trashed key restores it by normal upsert. In a full snapshot, only active remote keys form the authoritative mirror set; trashed remote keys are excluded, so full and incremental runs converge on the same local domain.

### 5.2 Initial and explicit full sync

If no watermark exists, incremental invocation is promoted to a full snapshot. Explicit `--full` also uses snapshot mode, regardless of an existing watermark.

Snapshot mode:

1. Fetches all pages with `includeTrashed=1` and without a conditional watermark.
2. Requires one stable target revision across all pages, including an empty result.
3. Parses the entire response set before touching SQLite.
4. Partitions active and trashed items using `data.deleted`.
5. In one transaction, upserts active remote items, removes every local item key absent from the active remote snapshot, and writes `R_target`.

The product-visible full-snapshot domain remains the existing non-trash ZotWatch `/items` domain. `includeTrashed=1` expands only the synchronization transport so trash state is explicit; it does not add trashed records to SQLite/profile. E3 will not introduce new item-type, collection, or write-back policy. Full sync does not need `/deleted`: active-set reconciliation already determines the final mirror and avoids making completeness depend on historical tombstone retention.

An empty remote snapshot is valid only after a successful response carrying a valid target revision. It removes all local item rows inside the final transaction. A network failure before that point cannot empty the local database.

### 5.3 Incremental sync

With committed `R_start`, incremental mode:

1. Fetches item changes with `since=R_start`, `includeTrashed=1`, and a first-request conditional header.
2. Follows pagination links while validating every response against `R_target`.
3. Fetches `/deleted?since=R_start`, never `R_target` or a page maximum.
4. Requires the deleted response revision to match `R_target`.
5. Partitions active item upserts from `data.deleted` trash removals.
6. Atomically applies active upserts, then trash/permanent-delete removals, then `last_modified_version=R_target`.

If a key appears in both changed items and tombstones for the same stable revision, deletion wins because it describes the final absence. Deleting an already absent key is a no-op.

## 6. Acquisition and validation boundary

Remote data is staged in memory as a mapping keyed by Zotero item key, including its explicit active/trashed state, plus a set of permanent-deletion tombstones. This is the smallest design that prevents partial commits without keeping a write transaction open during network operations. A staging table is not planned for E3 because it would add schema lifecycle and recovery states without improving the single-process correctness boundary. If real library sizes later make memory use unacceptable, a durable or temporary staging design can replace the collector behind the same commit contract.

Validation occurs before SQLite mutation:

- response bodies have the expected list/object shape;
- revision headers are present, numeric, non-negative, and stable;
- every item has a non-empty string key and valid non-negative item version before `ZoteroItem` projection;
- `data.deleted` is absent for active items or the documented `1`/`true` value for trashed items; another present value fails validation;
- tombstones are non-empty string keys;
- pagination next links cannot form a URL cycle;
- duplicate/replayed items with the same key select the highest item version;
- identical replays are ignored;
- conflicting payloads at the same item version fail the attempt instead of using response order as an implicit tie-breaker.

Malformed individual records fail the whole attempt. Skipping one bad object while advancing the library watermark would claim a complete mirror and make that object unreachable on the next incremental sync. Quarantine/degraded-watermark support is not part of E3.

The staged data contains normalized `ZoteroItem` objects and precomputed content hashes. It must not include API credentials in exceptions, logs, or returned stats.

## 7. SQLite transaction strategy

`ProfileStorage` will gain one sync-specific atomic apply operation; existing general-purpose methods and schema remain available for compatibility.

The operation will:

1. Start `BEGIN IMMEDIATE` only after all remote reads and validation succeed.
2. Upsert staged active items with the existing item-field projection and content hash behavior.
3. For incremental mode, remove staged trash-transition and permanent-tombstone keys in bounded batches.
4. For full mode, read current local keys inside the transaction, compute stale keys against the staged remote set, and remove them in bounded batches.
5. Write `metadata.last_modified_version = R_target` using the same connection and transaction.
6. Commit once. Any exception rolls back item rows, deletions, and metadata together.

Bounded deletes avoid SQLite parameter limits. The method returns actual inserted/changed/removed counts so replayed or unknown keys do not inflate success statistics.

E3 retains the existing `last_modified_version` metadata key and tightens its meaning to “last successfully committed Zotero library revision.” Existing databases therefore require no schema migration. Missing metadata triggers snapshot establishment. An invalid stored value produces a clear error; explicit full sync can recover by ignoring the invalid starting value and atomically replacing it only after a complete snapshot.

Embedding columns remain untouched by E3 upserts, matching the current storage behavior. Fixing or rebuilding computational state after source content changes remains E4 scope.

## 8. Failure, retry, and idempotency semantics

| Failure point | SQLite effect | Watermark effect | Retry behavior |
| --- | --- | --- | --- |
| First or later items page fails after HTTP retries | None | Remains `R_start` | Next run reads the same delta/full snapshot |
| Deleted endpoint fails | None | Remains `R_start` | Next run repeats items and deletions from `R_start` |
| Remote revision changes during pagination | None | Remains `R_start` | Discard staging and make a bounded fresh attempt |
| Malformed header, page, item, or tombstone | None | Remains `R_start` | Fail visibly; retry after remote/input correction |
| Pagination URL cycle | None | Remains `R_start` | Fail visibly instead of looping |
| SQLite apply fails | Full rollback | Remains `R_start` | Next run replays the same staged remote interval |
| Process exits during acquisition | None | Remains `R_start` | Next run starts cleanly |
| Process/SQLite failure during transaction | SQLite atomic rollback/recovery | Item rows and watermark remain mutually consistent | Next run starts from durable `R_start` |

Replaying a completed delta from an old test response is also idempotent: item upsert, tombstone deletion, and the same metadata replacement produce the same mirror. The real client normally requests only changes after the committed revision.

The ingestor will stop swallowing terminal request failures. It will raise a domain-specific exception so `profile`/`watch` fail instead of continuing into profile/ranking with an incomplete mirror. This is an intentional E3 correctness change and will be called out in release notes.

## 9. Compatibility

E3 preserves:

- `ZOTERO_API_KEY` and `ZOTERO_USER_ID` names and their existing settings lookup;
- the `zotwatch profile/watch` and `python -m src.cli profile/watch` entry points;
- `--full` and `--weekly` call compatibility;
- the existing SQLite `items` table and `metadata.last_modified_version` key;
- already stored user items and credentials; no Zotero reauthorization is needed;
- ranking, scoring, public candidate, output, config/provider registry, and non-sync E0/E1/E2 behavior.

Existing callers may continue reading `IngestStats.fetched`, `updated`, `removed`, and `last_modified_version`. New revision fields make the commit state explicit. Tests that intentionally assert BUG-I1/I2/I3 will be changed only alongside numbered E3 regression coverage. Original E0 golden files and unrelated fixtures will not be regenerated.

Compatibility does not preserve silent success after an incomplete sync, partial page commits, skipped tombstones, or stale rows after a full snapshot. Those are the named correctness fixes.

## 10. Expected modified files

Production files expected after plan approval:

- `src/ingest_zotero_api.py`: typed sync results/errors, revision-aware page/deletion responses, staged acquisition, stability validation, full/incremental state machine.
- `src/storage.py`: one atomic sync apply method and bounded deletion/reconciliation helpers; no table redesign.
- `src/cli.py`: only if needed to log explicit revision fields while preserving command signatures and nonzero failure propagation.

Test and documentation files expected:

- `tests/test_zotero_sync_e3.py` (new): focused state-machine, transaction, failure, retry, drift, malformed-record, and idempotency regressions.
- `tests/test_ingestion_dedupe.py`: replace only BUG-I1/I2/I3 observation assertions superseded by numbered E3 cases; retain unrelated mapping/dedupe and BUG-I4 coverage.
- `tests/test_pipeline_http.py`: update synthetic sync response headers/queries as required by the stricter protocol, without changing ranking/output expectations.
- `tests/helpers.py` only if the synthetic response helper needs explicit headers/URLs for the new cases.
- `tests/README.md`: map old BUG-I identifiers to E3 regression IDs and distinguish the remaining BUG-I4 observation.
- `docs/E3_SYNC_CORRECTNESS.md` (new): revision contract, operational recovery, intentional behavior changes, and release note.
- `.github/workflows/characterization.yml` and/or `.github/workflows/packaging.yml` only if a static E3 gate command is needed; no new service, secret, or network dependency.

No changes are planned under `zotwatch/config`, `zotwatch/providers`, ranking/candidate modules, harvester, workspace template, web, or reusable workflow code.

## 11. Regression test matrix

All HTTP tests use synthetic responses and real temporary SQLite databases. No test contacts Zotero.

| Case | Mode/setup | Expected assertions | Regression ID |
| --- | --- | --- | --- |
| Initial full sync | No watermark, empty/local-preloaded DB | Complete snapshot present; stale local rows absent; target revision committed atomically | E3-SYNC-003 |
| Explicit full sync | Existing watermark/items | Remote set exactly replaces mirror set; existing DB schema remains usable | E3-SYNC-003 |
| No-op sync | `R_start`, first response 304 | No deleted call, no item write, committed revision remains `R_start` | protocol contract |
| One added item | Delta since `R_start` | New row and `R_target` committed together | core incremental |
| One modified item | Existing key with newer item version | Source fields/hash updated; revision committed; embedding behavior unchanged | core incremental / BUG-I4 retained |
| One deleted item | Tombstone after `R_start` | Deleted request uses `R_start`; row removed with `R_target` | E3-SYNC-002 |
| Existing item moved to trash | Changed item has `data.deleted: 1` | Request uses `includeTrashed=1`; local row removed and `R_target` committed | E3-SYNC-009 |
| Trashed item restored | Changed item no longer has `data.deleted` | Active item is upserted into the local mirror and `R_target` committed | E3-SYNC-009 |
| Full sync with active and trashed items | Full transport includes both states | SQLite contains exactly the active set; result agrees with incremental transitions | E3-SYNC-003/009 |
| Add + modify + delete | One stable revision | All three effects and watermark commit in one transaction; delete wins on key overlap | E3-SYNC-002 |
| Multi-page sync | Two or more stable pages | Every next link fetched; one final SQLite transaction | core pagination |
| Second/middle page failure | First page valid, later page exhausts retry | Exception; item rows and watermark byte/semantically unchanged | E3-SYNC-001 |
| Deletion endpoint failure | All item pages valid | Exception; no staged items committed; watermark unchanged | E3-SYNC-004 |
| Retry after failed sync | Repeat after page/deletion failure | Starts from original watermark and commits complete result exactly once | E3-SYNC-001/004 |
| Full rebuild removes stale rows | Remote omits local key | Missing key removed only in successful final transaction | E3-SYNC-003 |
| Duplicate/replayed page | Same item appears more than once | One final row, deterministic highest version, stable counts | E3-SYNC-008 |
| Legacy vs applied stats | Unchanged/replayed item and unknown deletion | `updated` preserves per-record legacy count; new applied counters report actual row mutations | stats compatibility |
| Pagination cycle | `next` repeats a visited URL | Visible failure, no SQLite change | E3-SYNC-006 |
| Remote revision changes mid-sync | Later page or deleted response revision differs | Attempt discarded; fresh attempt succeeds, or bounded retry fails with no DB change | E3-SYNC-005 |
| Malformed individual item | One valid and one bad record | Whole attempt fails; valid sibling is not partially committed | E3-SYNC-006 |
| Malformed payload/header | Wrong JSON shape, absent/invalid revision | Visible failure; watermark and rows unchanged | E3-SYNC-006 |
| Empty full snapshot | Valid empty 200 response | Local item rows removed and target committed; a failed empty fetch never clears DB | E3-SYNC-003/006 |
| SQLite failure before watermark | Inject failure during apply | Entire transaction rolls back, including earlier upserts/deletes | transaction contract |
| Legacy DB compatibility | Existing E2-era schema and watermark | Reads watermark and syncs without migration or reauthorization | compatibility |

For the first implementation commit, the new correctness tests are added before production changes and are expected to fail against `v2-e2-baseline`. They will not be marked `xfail` or satisfied by changing golden files. The PR description will record which cases fail on the old implementation and pass after each fix.

## 12. Test gates

After each implementation commit, run the focused sync tests first. Before opening the PR, run:

1. New E3 sync suite and the existing ingestion/pipeline suites.
2. Full E0 characterization suite in its pinned environment.
3. E1 packaging/install gate, including wheel and editable installs.
4. E2 config/schema/provider registry tests and offline CLI validation.
5. A diff/hash check proving original E0 golden files and original E0 fixture files were not modified. E2's separate `tests/fixtures/config-v2/` additions remain intact.

No failing remote gate will be resolved by mass-updating golden expected output. Environment-only differences will be diagnosed before expected behavior changes.

## 13. Commit and PR boundaries

This plan is the only change in the current commit. After approval, E3 implementation will use reviewable commits in this order:

1. `test(sync): define E3 correctness regressions`  
   Add the failing regression matrix and test helpers. Record the intentional red result against `v2-e2-baseline`; do not use xfail or modify production code.
2. `fix(sync): stage a revision-consistent Zotero delta`  
   Add `since=R_start`, typed page/deletion results, header/payload validation, duplicate handling, stable-revision checks, bounded drift restart, and propagated failures.
3. `fix(storage): commit Zotero mirror updates atomically`  
   Add the single SQLite apply transaction, full snapshot reconciliation, tombstone ordering, rollback behavior, and compatible watermark handling.
4. `docs(sync): record E3 behavior changes and verification`  
   Update the characterization map/release note, any narrowly required CLI logging and CI gate, and final local/remote evidence.

If the natural dependency between the state machine and storage makes commits 2 and 3 temporarily fail, each commit will still remain structurally reviewable and the final branch will be green. Production sync, tests, and docs stay in one E3 PR; no harvester, candidate, ranking, AI, state-artifact, workflow-template, or Cloudflare changes enter it.

## 14. Rollback strategy

E3 introduces no irreversible schema migration. Rolling back the E3 code to `v2-e2-baseline` leaves the existing `items` and `metadata` tables readable. The tightened watermark uses the same key and integer representation.

If E3 has completed a sync before rollback, the database contains a more accurate mirror at the committed revision. Old code can read it. Because old code has unsafe failure semantics, the operational rollback procedure is:

1. Stop scheduled sync during rollback.
2. Pin the engine/caller to `v2-e2-baseline` only if needed to restore execution.
3. Preserve the SQLite file for diagnosis.
4. After restoring E3 or a follow-up fix, run an explicit full sync to re-establish a complete remote snapshot.

The E3 code path itself rolls back failed SQLite transactions automatically. No automatic downgrade, database deletion, credential change, Zotero reauthorization, or golden rewrite is part of rollback.

## 15. Explicit exclusions

E3 will not implement profile incremental rebuild, embedding/FAISS invalidation or persistence, clustering, AI, recommendation/scoring changes, public candidate changes, reusable workflows, workspace-template changes, Cloudflare control-plane work, Zotero OAuth, Zotero write-back changes, or harvester changes.
