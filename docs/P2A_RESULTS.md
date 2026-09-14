# P2A — Central Reusable Workflow Results

P2A starts from `v2-e5-baseline`
`c439bb9046b8c863b12348de8e90e735017ba7e3` and adds the central
GitHub.com reusable workflow boundary.  The final `S_P2A` merge SHA and
baseline tag are recorded after PR #10 is merged.

## Implemented contract

- `.github/workflows/run.yml` is a compute/private-artifact reusable workflow.
  It checks out the caller at `github.sha`, checks out the public central
  repository at `job.workflow_sha`, and verifies engine Git HEAD before
  execution.
- The default compute token has only `contents: read` and `actions: read`.
  Provider credentials are statically declared from the E2 fixed-slot model;
  the workflow does not use `secrets: inherit`.
- `.github/workflows/publish-pages.yml` is a separate opt-in permission domain.
  Only it requests `pages: write` and `id-token: write`.
- Personal computational state uses the private
  `zotwatch-state-checkpoint-v1` workflow artifact.  No Zotero-derived file is
  stored in Actions cache.  Restore examines at most 15 completed trusted runs
  of the same private caller repository/workflow/ref and accepts the first
  fully valid checkpoint, independent of a later Pages-only failure.
- Checkpoints contain a SQLite backup, `computational/current.json`, and only
  its referenced immutable E4 generation.  Closed metadata, file hashes,
  SQLite integrity, library binding, E4 state binding and source result status
  are checked before installation.
- E5 RunResult/private manifest are the output authority.  Recommendation,
  private operational and checkpoint artifacts are separate; Pages receives
  only the revalidated RSS/HTML/JSON allowlist.
- Python 3.11.11, runtime constraints, third-party Actions and the default
  local embedding model revision are pinned.  Dependency download caching is
  limited to public runtime packages and excludes workspace/state/reports.

## Validation evidence

- Local full gate: 230 passed, 16 skipped.
- Local focused P2A gate: 23 passed.
- Local sdist/wheel build includes `zotwatch.workflow` and
  `state-checkpoint-v1.schema.json`.
- PR #10 candidate `5831ad753b50d26123a56382667c5a3ffb88cb57`:
  E0 characterization push/PR gates passed; packaging/install push/PR gates
  passed.
- Disposable private cross-repository caller:
  `Yorks0n/zotwatch-p2a-smoke-20260914`, run `34828908556`, passed a
  credential-free `workflow_dispatch` validate call pinned to
  `460e5773aad938a9cd7c2281628363a250694a7a`.  The caller and engine commits
  differ, and the reusable job completed the caller checkout, exact central
  checkout, identity assertion, offline config validation and skipped all
  state/credential/output steps.

No pre-P2 fixture or golden was modified.  The only new fixture is the P2
third-party Action pin registry.  Ranking, Zotero synchronization, candidate
fetch semantics, E4 persistence and E5 result schemas are unchanged.

## Seal record

- PR: #10
- `S_P2A`: pending merge
- annotated tag: pending `v2-p2a-baseline`
- final merged-SHA cross-repository smoke: pending

P2B must not start until the three pending seal fields are complete.  Its
caller workflow must use the literal `S_P2A`; a branch or tag is not an
acceptable engine reference.
