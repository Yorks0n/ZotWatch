# E4 red baseline

Recorded 2026-09-11 before any E4 production change. The production tree is exactly
`v2-e3-baseline` (`8ceec503f81f701ead156c5695b3d115b1cac537`); only the approved E4 plan and
the four regression tests are present on the branch.

Command:

```console
/private/tmp/zotwatch-e0/bin/python -m pytest -q tests/test_computational_state_e4.py
```

Expected result: exit code 1, **4 failed**.

| Regression | E3-baseline failure |
| --- | --- |
| `test_profile_build_publishes_versioned_generation` | `ProfileArtifacts` has no manifest and profile/FAISS are top-level mutable files. |
| `test_content_change_invalidates_legacy_embedding` | a changed embedding input retains the old SQLite embedding BLOB (BUG-I4). |
| `test_ranker_rejects_generation_from_another_model` | `WorkRanker` loads an index built by another model without compatibility validation. |
| `test_watch_ensures_state_after_sync` | `watch` syncs SQLite but never ensures/rebuilds computational state (BUG-W1). |

This red evidence does not change or regenerate any E0 fixture/golden. Later E4 tests will add
failure/recovery, revision drift, manifest corruption, atomic publication, installation and lease
ownership coverage behind the same behavioral contract.
