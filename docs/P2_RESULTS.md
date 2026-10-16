# P2 — GitHub Personal Compute/Data Plane Results

P2 starts from the E5 baseline
`c439bb9046b8c863b12348de8e90e735017ba7e3` and is split across the public
central engine repository and the private personal-workspace template.

## Sealed revisions

- Executable central workflow/engine revision (`S_P2A`):
  `7205a2b6f01851aed8382025289542eb81f8dabe`
- Central annotated checkpoint tag: `v2-p2a-baseline`
- Thin workspace-template merge revision (`S_TEMPLATE`):
  `87f55957299cfc22146fa9af723029cb35ff4301`
- Template PR: [Yorks0n/zotwatch-workspace-template#1](https://github.com/Yorks0n/zotwatch-workspace-template/pull/1)
- Template repository setting: private GitHub template repository, default branch
  `main`

The template caller remains pinned literally to `S_P2A`. It does not pin the
later P2A documentation commit or this P2 sealing commit.

## Template and static contract

The merged template contains exactly four files:

- `zotwatch.yaml`
- `.github/workflows/watch.yml`
- `README.md`
- `.gitignore`

The default caller requests only `contents: read` and `actions: read`, maps
only `ZOTERO_USER_ID` and `ZOTERO_API_KEY`, and has no Pages/OIDC permission,
PR/push trigger, `secrets: inherit`, floating workflow ref, provider secret
bundle or engine-ref input. Basic config validation passed with RSS, HTML and
JSON enabled, publication disabled, local embedding, legacy-v1 ranking and no
AI or caller Supabase credential. The central focused P2 contract suite passed
with 23 tests.

## Cross-repository acceptance

Two independent private disposable workspaces were created from the candidate
template. Each stored only its own two Zotero repository secrets. The central
ZotWatch repository and CI stored no personal credentials.

| Scenario | Workspace A | Workspace B | Result |
| --- | --- | --- | --- |
| First normal run from empty state | [34841990281](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34841990281) | [34842022714](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34842022714) | passed |
| Restore prior valid checkpoint | [34843362261](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34843362261) | [34843369861](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34843369861) | passed |
| Manual full rebuild then authoritative watch | [34843863119](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34843863119) | [34843873038](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34843873038) | passed |
| Profile-only diagnostic | [34844749249](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34844749249) | [34844765875](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34844765875) | passed |
| Checkpoint absent, rebuild from Zotero | [34845156931](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34845156931) | [34845171508](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34845171508) | passed |
| Invalid checkpoint rejected, clean rebuild | [34846278677](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34846278677) | [34846287537](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34846287537) | passed |

Every normal/full run produced a valid E5 `run-result-v1`, private run
manifest, RSS, HTML, recommendation JSON and a bounded private
`zotwatch-state-checkpoint-v1`. Profile-only did not publish recommendation
artifacts. Full rebuild produced distinct profile and watch results; the watch
result remained the recommendation authority.

Restore discovery selected an exact artifact from a prior trusted run of the
same repository, workflow and ref. Missing and malformed artifacts became
restore misses, then rebuilt successfully. Cross-workspace checkpoint import
was rejected by repository/run namespace metadata. E3/E4 validation remained
the final compatibility authority.

Artifact inspection confirmed that publishable archives contain only
`feed.xml`, `report.html` and `recommendations.json`. Private operational
results and checkpoints stayed in separate private artifacts. The committed
Git trees contain no SQLite, FAISS, embedding, profile, private manifest,
machine-result or generated report data.

## Identity, schedule, Pages and rollback

- The P2A post-merge cross-repository validate smoke passed in run
  [34830736399](https://github.com/Yorks0n/zotwatch-p2a-smoke-20260914/actions/runs/34830736399).
- A real `schedule` event used the normal caller path and restored a valid
  checkpoint in run
  [34881606336](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34881606336).
- Every audited compute run asserted engine HEAD and reusable workflow SHA as
  `S_P2A`; caller commit SHAs were distinct from the engine SHA.
- The opt-in Pages run
  [34846931442](https://github.com/Yorks0n/zotwatch-p2b-acceptance-a-20260914/actions/runs/34846931442)
  completed compute, result validation, private/report/checkpoint uploads and
  closed allowlist materialization. Its isolated Pages job alone failed at
  repository Pages configuration because Pages was not enabled. All private
  compute artifacts remained available. The default caller permission graph
  never contained Pages or OIDC scopes.
- The rollback canary ran the prior compatible workflow SHA
  `f14269932eacf84e9055cd08679971a49db93d5e` successfully in run
  [34846943731](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34846943731),
  then restored `S_P2A` and passed run
  [34847911986](https://github.com/Yorks0n/zotwatch-p2b-acceptance-b-20260914/actions/runs/34847911986).

The acceptance repositories recorded only sanitized IDs, revision identities,
artifact names/digests and contract conclusions. No Zotero user identifier,
item title or metadata, API key, raw private manifest, custom credential or
endpoint is included here.

## Boundary conclusion

P2 demonstrates that a private thin workspace can run Basic recommendations
manually or on schedule using the exact public central engine/workflow
revision, without Cloudflare, AI credentials, a caller Supabase secret or an
engine fork. Personal computational state travels only as bounded private
workflow artifacts and remains disposable: expiration, absence, corruption or
incompatibility causes a safe Zotero rebuild rather than stale ranking.

The central ZotWatch repository is public for the P2 v1 cross-repository
checkout/authentication model. Changing it to private requires a new access
and authentication review; the current caller token is not assumed to read an
arbitrary private central repository.
