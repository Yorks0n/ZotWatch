# E1 packaging/path verification

Base: `v2-e0-baseline` (`217acfd40bd4da0c3410b7b3a360e3829dcfef47`). E0 goldens, synthetic fixtures, original test files and their dependency lock remain unchanged.

Local verification on Python 3.11.11/macOS arm64:

- Lightweight suite: **74 passed** (the original 65 E0 cases plus 9 path/resource cases). The 7 isolated-installation cases are explicitly skipped without packaging environment configuration.
- Dedicated installation suite: **7 passed**, with `E1_INSTALLATION_REQUIRED=1`; no skips. Wheel and editable each use a separate virtual environment with full runtime dependencies and passing `pip check`.
- Both installed entry points run outside the checkout. Synthetic profile/ranking and complete RSS/HTML results match the unchanged E0 goldens. HTTP/model calls are replaced only in the subprocess test probe.
- The wheel is built from the sdist, contains the exact original SJR CSV, and excludes tests, config, databases, indexes and reports. Bundled metrics are explicit; missing legacy metrics remain missing.
- Source review also compares the AST of scoring, candidate fetch/cache behavior and profile summary functions to E0; those functions are unchanged. The original daily workflow, requirements, Zotero sync, public API behavior and harvester are unchanged.

The two local full installs resolved sentence-transformers 6.0.1 and torch 2.14.0 while retaining E0-constrained numpy 1.26.4, faiss-cpu 1.9.0.post1 and pydantic 2.10.6. This records an installation check, not a new production dependency lock or a model-quality claim. Model downloads are disabled during tests.

## Reproduce the dedicated suite

Install `tests/requirements.txt` and `tests/packaging-requirements.txt` in a test-runner environment. Build with `python -m build --no-isolation --outdir /tmp/e1-dist`, which builds the wheel from the sdist. Install that wheel with dependencies into one fresh venv and install the checkout with `pip install -e .` into another; neither installation should rely on PYTHONPATH. Both environments also need the test dependencies for the probe assertions.

```sh
E1_INSTALLATION_REQUIRED=1 \
E1_DIST_DIR=/tmp/e1-dist \
E1_WHEEL_PYTHON=/tmp/e1-wheel/bin/python \
E1_EDITABLE_PYTHON=/tmp/e1-editable/bin/python \
PYTHONHASHSEED=0 TZ=UTC OMP_NUM_THREADS=1 \
python -m pytest -c tests/pytest.ini -q
```

With these environments supplied, all **81 cases** are collected with no installation skips. CI uses [.github/workflows/packaging.yml](../.github/workflows/packaging.yml) for this check and retains the separate lightweight characterization workflow. On Linux the installation job selects the official CPU torch wheel before resolving the original dependency ranges. A missing packaging environment fails when `E1_INSTALLATION_REQUIRED=1`.

`git diff --exit-code v2-e0-baseline -- tests/goldens tests/fixtures` is a separate CI gate. No test rewrites expected recommendation behavior to accommodate installation differences.
