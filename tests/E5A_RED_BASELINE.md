# E5A expected-red baseline

Starting production is exactly `v2-e4-baseline` (`5afdaa603ec5e9978ea871505c6233c33e23b7a3`). The approved E5 plan/clarifications and these runtime regression tests are the only changes.

Command:

```text
PYTHONHASHSEED=0 TZ=UTC OMP_NUM_THREADS=1 /private/tmp/zotwatch-e0/bin/python -m pytest -q \
  tests/test_runtime_config_e5.py tests/test_runtime_preflight_e5.py tests/test_runtime_cli_e5a.py
```

Expected result: pytest exits 2 during collection with two `ModuleNotFoundError: No module named 'zotwatch.runtime'` errors. This proves the E4 production tree has neither a Basic v2 effective-runtime adapter nor runtime preflight; the public CLI still defers all v2 execution.

No E0 fixture/golden was changed.
