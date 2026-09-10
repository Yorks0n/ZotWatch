# E3 Zotero sync correctness contract and release note

Status: implementation complete locally; remote PR gates pending. E3 starts from `v2-e2-baseline` (`2c276da8696618fcd4acd4b34eaf8b1b900219a4`).

## Mirror invariant

`metadata.last_modified_version` is the last Zotero library revision for which ZotWatch successfully acquired and committed a complete local mirror update. Item rows and this watermark change in one SQLite transaction. If acquisition, validation, revision stability, deletion retrieval, or SQLite apply fails, the prior rows and watermark remain durable.

The four revision values reported by ingestion are:

- `start_revision`: the durable watermark read before the run;
- `target_revision`: the revision established by the first versioned items response;
- `observed_revision`: the matching revision last observed after all required pages and deleted data;
- `committed_revision`: the target written atomically with the mirror update.

On success, target, observed, committed, and legacy `last_modified_version` identify the same revision. An incremental 304 is a successful no-op at `start_revision`.

## Transport and local domains

Both full and incremental item reads use `includeTrashed=1`. This follows Zotero's [official sync sequence](https://www.zotero.org/support/dev/web_api/v3/syncing) and lets the engine observe move-to-trash and restore transitions. The API's direct editable JSON state is used: `data.deleted: 1` means trashed; after restore the property is absent. Zotero's official Data Server tests exercise the same [include-trash and restore representation](https://github.com/zotero/dataserver/blob/476ed12c18cbd431170346702882d091710f5f61/tests/remote/tests/3/item.test.js#L2089-L2215).

The transport domain may contain trash, but the ZotWatch local mirror and profile domain contains active items only:

- moving an existing item to trash removes its local mirror row;
- restoring an item upserts it into the mirror again;
- full sync reconciles SQLite against only the active keys from the complete transport snapshot;
- permanent deletion tombstones and trash transitions both remove rows, with removal winning if the same key is also present as an active update.

## Full and incremental behavior

When no watermark exists, a normal invocation establishes a full snapshot. Explicit `--full` does the same while retaining the old CLI contract. All pages are staged and validated before SQLite changes. Full sync does not clear the database before network access and does not depend on historical deletion tombstones; it atomically removes local keys absent from the completed active remote snapshot.

Incremental item retrieval sends `since=<start_revision>`, `includeTrashed=1`, and the existing first-request `If-Modified-Since-Version`. The deleted endpoint always receives the same immutable start revision. Every 200 item page and deleted response must report the target `Last-Modified-Version`. If Zotero changes revision during acquisition, the staging area is discarded and retrieval restarts from the original durable watermark, up to the bounded retry limit.

Malformed payloads, missing/invalid revision headers, invalid item keys/versions/trash markers, conflicting same-version duplicates, and pagination cycles fail the whole attempt. Valid records are not partially committed around an invalid sibling.

## SQLite boundary

Remote data is staged in memory; no database write transaction is held across network requests. After completeness checks, `ProfileStorage.apply_zotero_sync()` executes:

1. `BEGIN IMMEDIATE`;
2. active-item inserts and changed-row updates;
3. trash/tombstone deletion, or full active-key reconciliation;
4. `metadata.last_modified_version` replacement;
5. one commit, with rollback on any exception.

The existing tables and metadata key are unchanged, so current SQLite files need no schema migration. Explicit full sync can recover from an invalid stored watermark only after a complete remote snapshot. The embedding column is intentionally preserved; embedding invalidation and FAISS/profile state remain outside E3.

## Stats compatibility

Legacy fields keep their established operational meaning:

- `fetched`: valid transport item records observed, including replayed and trash records;
- `updated`: valid active item records accepted for upsert, including unchanged/replayed records;
- `removed`: unique removal intents for incremental runs and reconciled local rows for full runs.

New fields report actual SQLite mutations: `applied_inserted`, `applied_changed`, and `applied_removed`. An unchanged replay can therefore report `updated=1` and `applied_changed=0`; an unknown tombstone can report `removed=1` and `applied_removed=0`.

## Intentional behavior changes

| ID | Change |
| --- | --- |
| E3-SYNC-001 | Middle-page failure now fails the run without partial rows or watermark advancement. |
| E3-SYNC-002 | Deleted data is requested from the last committed revision, fixing BUG-I1. |
| E3-SYNC-003 | Full sync removes stale and trashed local rows after a complete snapshot, fixing BUG-I3. |
| E3-SYNC-004 | Deleted-endpoint failure now aborts the whole staged attempt. |
| E3-SYNC-005 | Mixed remote revisions are discarded and retried instead of taking their maximum. |
| E3-SYNC-006 | Missing revisions and malformed pages/records are fatal to the attempt. |
| E3-SYNC-007 | Incremental items use the required `since` revision. |
| E3-SYNC-008 | Replayed items are staged deterministically by key/version and applied idempotently. |
| E3-SYNC-009 | Trash and restore transitions are observed through `includeTrashed=1` while trash remains excluded from the product mirror. |

Terminal network failures now propagate as `ZoteroSyncError`; `profile` and `watch` stop instead of continuing with an incomplete mirror. This is intentional. Zotero API key/user ID names, authorization, installed and legacy CLI entry points, existing SQLite schema, and non-sync behavior remain compatible.

## Recovery and rollback

A failed run can be retried normally because its durable start revision did not move. If a stored watermark is malformed or the mirror is suspect, run an explicit full sync; the old mirror remains until the complete replacement is ready.

E3 adds no irreversible schema migration. Code can be pinned back to `v2-e2-baseline` while preserving the database for diagnosis, though the E2 sync path has the correctness defects listed above. After restoring E3, run full sync to re-establish the invariant.

## Regression evidence

The tests-only commit failed against the unchanged E2 production tree with **25 failed, exit code 1**; see [E3_RED_BASELINE.md](../tests/E3_RED_BASELINE.md). The final verification record will be added after E0/E1/E2/E3 local and remote gates complete. Original E0 goldens are not regenerated.

After implementation, the focused sync/ingestion/pipeline set passes **46 tests**. The local forced packaging run builds a fresh sdist and wheel, invokes both wheel and editable engines outside the source checkout, runs E0/E1/E2/E3 together, and reports **167 passed, 0 skipped**. Remote PR evidence remains pending until the branch is pushed.
