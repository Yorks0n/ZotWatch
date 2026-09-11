# E3 regression red baseline

Captured 2026-09-10 after test commit `9d893673013f4079cffdce496a9136e24eaf534b`.

The production tree is identical to `v2-e2-baseline` (`git diff --exit-code v2-e2-baseline -- src` exited 0). Only the approved E3 plan and the new regression suite precede this capture.

Command:

```sh
PYTHONHASHSEED=0 TZ=UTC OMP_NUM_THREADS=1 \
  /private/tmp/zotwatch-e0/bin/python -m pytest \
  -c tests/pytest.ini -q tests/test_zotero_sync_e3.py
```

Result: **25 failed, 0 passed, exit code 1**. The three warnings are the existing FAISS/SWIG deprecation warnings.

The failures expose the intended E3 behavior changes:

- item delta requests lack `since` and `includeTrashed`;
- deleted requests use the newly observed revision instead of the last committed revision;
- terminal page/deletion errors are swallowed and partial writes survive;
- full sync does not reconcile stale or trashed rows;
- remote revision drift is combined rather than restarted;
- malformed payload/header/item boundaries are not represented as sync failures;
- SQLite item changes and watermark are not one transaction;
- revision and actual-apply counters do not yet exist;
- 304 handling still proceeds to the deleted endpoint.

This red capture is evidence, not an accepted CI state. The final E3 branch must make the suite green without marking cases xfail or changing unrelated E0 fixtures/goldens.
