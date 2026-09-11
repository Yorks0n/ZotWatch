# E5 — Runtime & Result Contract Implementation Plan

日期：2026-09-11

状态：**plan only；等待审阅，尚未修改 production code。**

起点：`v2-e4-baseline` / `5afdaa603ec5e9978ea871505c6233c33e23b7a3`。

## 1. 目标、边界与 PR 拆分决定

E5 是 P2 reusable workflow / thin workspace 前最后一个 engine 阶段。它必须同时关闭两个边界：

1. 让通过 E2 schema/semantic validation 的 **Basic v2** `zotwatch.yaml` 真正进入已有 E3 sync、E4 state、candidate、dedupe、legacy rank 和 output pipeline；
2. 给该 pipeline 增加 versioned recommendation JSON、private run manifest、稳定 stage/error/exit semantics 和失败安全的 output publication。

分析当前代码后，计划将 E5 实施拆成两个连续、独立审阅的 PR：

- **E5A — Basic v2 runtime bridge & preflight**：只建立 config source → effective runtime mapping、capability/credential preflight 和 v2/legacy 同 pipeline 等价执行；不引入新 result schema，也不改 output publication。
- **E5B — Result/run contracts & safe publication**：在 E5A 已证明 execution equivalence 后，加入 recommendation/run schemas、stage recorder、symbolic errors、exit policy、candidate outcome metadata 和 output generation publication。

拆分理由是两部分具有不同的 correctness proof。E5A 的核心证据是 legacy/v2 effective settings 与结果内容等价；E5B 的核心证据是任一失败点不会发布混合或失败产物。分开后每个 PR 都可独立回退，也避免在同一 diff 中同时改 config dispatch、全 pipeline orchestration 和全部 writer commit boundary。

**E5A 与 E5B 都属于 E5，且两者必须在进入 P2 前合入、打门禁并封板。** E5A 可使用内部 checkpoint tag `v2-e5a-baseline`；只有 E5B 合入后才创建最终 `v2-e5-baseline`。不把 v2 execution gap、run manifest 或 publication 留给 P2/Web。

E5 不实现 reusable workflow、workspace-template、GitHub artifact/cache transport、Cloudflare、Zotero OAuth、AI 网络 adapter、AI rerank/summary、clustering、新 ranking、durable feedback、harvester 修复或 public candidate API redesign。

### 1.1 Approved implementation clarifications

- **E5A output capability checkpoint**：E5A 只执行 `rss` / `html`。虽然 E2 schema 已允许 `json`，但 E5A 在 preflight 将任何 enabled `json` request 明确报告为 `OUTPUT_FORMAT_UNAVAILABLE`，使用 capability/credential exit category 3，并在 Zotero sync、model load、candidate network 或其他 recommendation side effect 前停止。E5A 不 silent-ignore JSON，也不提前实现半套 JSON contract。E5A equivalence fixture 使用只含 RSS/HTML 的 v2 config；E5B 合入后 registry/capability table 才把 JSON 标为 supported。`v2-e5a-baseline` 只是内部 checkpoint，不满足最终 P2 JSON requirement。
- **Complete legacy score provenance**：recommendation JSON v1 分别输出 `similarity`、`recency`、`citations`、`altmetric`、`journal_quality`、`author_bonus`、`venue_bonus` 七个 component。每项包含 `raw_value`、`weighted_contribution` 和 `input_available`；正常浮点容差内 `score == sum(weighted_contribution)`。E5B 可给内部 `RankedWork` 增加 additive `altmetric_score` / provenance 字段，但不改变公式、weights、threshold 或排序。
- **Explicit preprint transport**：E5B 给 `CandidateWork` 增加 optional `is_preprint`。`public-api-v1` 原样保留 payload 的明确 boolean；明确的 arXiv/bioRxiv/medRxiv adapter 设置 `true`；无法确定时保持 `null`。JSON projector 不按标题、类型、URL 或“不是已知 preprint source”猜 false。`public_id` 继续从当前 whitelist metadata 显式投影，不复制 `extra`。
- **P2 artifact authority**：immutable successful output generation、finalized artifact list 和 `latest-success` pointer 是 machine authority。stable report paths 仅为兼容/用户 alias。`RunResult` 与 private run manifest 给 P2 的 references 必须指向 immutable generation 内的相对路径，并包含 hash、size、media type；P2 后续按 finalized whitelist 上传/发布，不扫描 reports、不看 mtime、不依赖 aliases 的瞬时一致性。

## 2. 当前 runtime / output flow

当前 public console entry `zotwatch.cli.main()` 有两条行为：

- `zotwatch config validate` 调用 E2 loader，离线校验 schema/semantics；
- `profile` / `watch` 先检测 config source。发现 `zotwatch.yaml` 时直接返回 `CONFIG_V2_EXECUTION_DEFERRED`；legacy 时转交 `src.cli.main()`。

Legacy `src.cli.main()` 当前执行：

```text
resolve workspace/state/reports
→ load .env
→ load three legacy YAML files into src.settings.Settings
→ open profile.sqlite
→ profile or watch
→ close SQLite
```

`profile`：

```text
acquire E4 run lease
→ E3 Zotero sync
→ ensure/build E4 computational generation
→ release lease
→ log paths
```

`watch`：

```text
acquire E4 run lease
→ E3 Zotero sync
→ ensure/build E4 generation
→ fetch candidates using the same profile summary
→ dedupe against the committed mirror
→ rank with the same StateHandle
→ release lease
→ seven-day filter
→ legacy preprint cap
→ top limit
→ optionally write feed.xml
→ optionally write one dated/empty HTML file
→ optionally push to Zotero
```

Current output and error limitations:

- RSS/HTML writers write directly to stable paths. If a later writer or Zotero push fails, earlier files may already have replaced known-good output.
- There is no public recommendation JSON or private operational run manifest.
- Candidate source failures are reduced to log messages plus a list; callers cannot distinguish a complete empty result, a partial result, or stale-cache fallback.
- Successful functions return `None`; P2 would have to parse logs and guess artifact paths/status.
- `argparse` gives parse/config failures code 2, while most runtime exceptions escape and normally become process code 1. There is no stable runtime category or symbolic error.
- Config/capability/credential failures do not produce a sanitized machine-readable record.
- E4 computational-state generations are atomic, but report files have no analogous run generation or success pointer.

## 3. E5A: one effective runtime model, one pipeline

### 3.1 Data model and ownership

Add an internal, immutable `EffectiveRuntimeConfig` that contains only values consumed by the implemented pipeline:

```text
EffectiveRuntimeConfig
  source: legacy | v2
  config_schema_version: null | 2
  config_fingerprint_sha256
  settings: src.settings.Settings
  embedding_provider/model
  ranking_policy
  top_n
  max_preprint_ratio
  output_formats
  publish_requested
  feature_routes
  compatibility flags
```

`src.settings.Settings` remains the compatibility shape consumed by E3/E4/fetch/ranker. The new adapter owns construction of that shape; production modules do not inspect YAML source or implement a second v2 pipeline. Runtime-only values that do not belong in legacy `Settings` remain on `EffectiveRuntimeConfig`.

`ExecutionRequest` holds command-line run choices separately from Git config:

```text
command: profile | watch
full / weekly
workspace/state/reports paths
strict
legacy rss/report/top/push switches
journal metrics selection
machine-result mode
```

The loader calls `detect_config_source()` exactly once. A workspace containing both modes still fails with `CONFIG_MIXED_MODES`; no field-level merge, fallback, or “prefer v2” behavior is allowed.

### 3.2 Legacy mapping boundary

Legacy runtime continues to use `LegacyConfigAdapter.load().settings` without round-tripping through the lossy legacy → v2 projection. `LegacyMappingReport` remains a migration/reporting aid and never becomes execution authority.

For legacy invocations:

- the existing three files, `${ENV}` expansion, fixed Secret names, advanced public endpoint/key override, source fallbacks, scoring customization and direct-fetch mode continue to work;
- `python -m src.cli ...` remains a compatibility entry point;
- existing `--rss`, `--report`, `--top`, `--push`, `--full`, `--weekly`, path and journal-metrics behavior remains accepted;
- existing RSS GUID and HTML content/path tests remain authoritative.

Shared runtime functions may return richer internal results, but the compatibility wrapper preserves legacy success behavior and does not require conversion to `zotwatch.yaml`.

### 3.3 Basic v2 → runtime mapping

E5A accepts only the capabilities already constrained by config schema v2:

| v2 field | Effective runtime mapping |
| --- | --- |
| `zotero.library_type=user` | Existing E3 user-library ingestor. User ID and API key come from engine-owned fixed Zotero credential definitions; YAML contains neither name nor value. |
| `candidates.provider=public-api-v1` | `PublicCandidatesApiConfig(enabled=true)` populated from packaged `PublicCandidateConnection`; caller supplies no Supabase key/endpoint. |
| `candidates.sources` | Existing source enable flags used by `CandidateFetcher`, preserving the current public-v1 request and validated legacy top-venue compatibility path. |
| `candidates.window_days=7` | Existing seven-day fetch/filter policy. |
| `embedding.provider=local` and fixed model | Existing `TextVectorizer`; no remote embedding/provider adapter. |
| `ranking.policy=legacy-v1` | Exact frozen legacy weights, thresholds, decay rules and whitelist defaults used by the ordinary legacy projection. No scoring formula changes. |
| `ranking.top_n` | Run top limit when no legacy compatibility wrapper is involved. |
| `ranking.max_preprint_ratio=0.3` | Existing deterministic `_limit_preprints` policy. |
| `outputs.formats` | Requested local render set for v2 `watch`; E5A supports RSS/HTML and rejects JSON before side effects，E5B enables JSON；`profile` produces no recommendation outputs. |
| `outputs.publish` | Recorded publish intent only. E5 performs no Pages/GitHub publication; P2 consumes this flag. |
| disabled AI features | No route, credential or network requirement. |

The v2 adapter uses the packaged journal/SJR resource, so an independent thin workspace does not need an engine source `data/journal_metrics.csv`. The packaged public connection is copied into an existing runtime settings object without exposing its publishable key in config, logs, repr, result, or manifest.

The fixed legacy-v1 scoring values live in one runtime-policy constant/module. E5A will remove duplicate numeric definitions between legacy projection and v2 runtime construction by importing that single constant; this is metadata consolidation, not a scoring change.

### 3.4 CLI option rules for v2

Canonical v2 execution is:

```text
zotwatch profile --workspace <workspace> [--full|--weekly]
zotwatch watch --workspace <workspace> [--strict]
```

For v2 `watch`, `top_n`, preprint ratio and output formats come from `zotwatch.yaml`. During E5A, `json` maps to `OUTPUT_FORMAT_UNAVAILABLE` before side effects; RSS/HTML continue. Existing legacy-only `--rss`, `--report`, `--top`, `--push` and custom journal-metrics switches are rejected as `CONFIG_OPTION_UNSUPPORTED` before Zotero sync when used with v2; this avoids an undocumented effective-config merge. P2 will invoke the canonical no-overlay form after E5B enables JSON.

Legacy commands keep those switches unchanged. A future explicit v2 override contract can be versioned separately. E5 may add a legacy `--json` opt-in without changing existing invocations; Basic v2 already requests JSON through `outputs.formats`.

`outputs.publish` does not cause network publication in E5. It only appears as sanitized run intent so P2 can decide whether to publish the output-generation whitelist.

### 3.5 Side-effect ordering

The public coordinator resolves paths, detects/loads config, resolves routes, and completes preflight before opening a write transaction, contacting Zotero/public candidates, loading the vector model, or creating a computational generation.

Writing a private failed run manifest after valid state/report paths are known is allowed and is not considered recommendation-pipeline side effect. A malformed path request that prevents resolving a safe state directory cannot promise a manifest; it still returns the stable config/path exit category.

After preflight, both sources enter the same existing functions:

```text
EffectiveRuntimeConfig + ExecutionRequest
→ E3 sync
→ E4 ensure/build
→ CandidateFetcher
→ DedupeEngine
→ WorkRanker
→ current filters
→ E5 output coordinator
```

No v2-specific sync, state builder, fetcher, deduper, ranker, writer, cache, or SQLite schema is introduced.

## 4. Credential/runtime preflight

### 4.1 Three independent facts

Preflight reports these separately for each requirement:

- `configured`: the fixed engine-owned credential slot has a non-empty runtime value with the expected coarse shape;
- `runtime_supported`: the selected provider/protocol adapter is implemented in this engine revision;
- `verification`: `not_requested | verified | failed | unsupported` for an explicit upstream check.

“Secret exists” is never worded as “credential is valid.” Default preflight is offline and sets upstream verification to `not_requested`.

### 4.2 Fixed requirements and redacted output

The engine registry owns fixed runtime lookups for:

- Zotero user-library identity;
- Zotero API credential;
- preset provider credentials;
- `custom-1` … `custom-4` structured credential slots.

Config and machine output use safe requirement IDs such as `zotero.identity`, `zotero.read`, `feature.rerank`, and `feature.summary`. They never emit environment names, GitHub Secret names, credential values, Custom base URLs, or complete service records.

Basic v2 with AI disabled requires only Zotero identity/read credentials. The packaged public pool connection is an engine dependency, not a user credential requirement; preflight explicitly reports `user_credential_required=false` without printing the internal key.

For every enabled AI route, preflight consults the E2 registry. All E2 preset and Custom protocol entries currently have `adapter_status=unimplemented`, so execution returns `CAPABILITY_UNAVAILABLE` with exit category 3 **before Zotero sync, candidate fetch, model load, or other recommendation side effects**, even when a corresponding fixed slot happens to be populated. E5 does not add AI requests.

Issue precedence is deterministic: config/path invalid → output/provider capability unavailable → credential missing/malformed → explicit upstream verification failure. The preflight report can list all sanitized requirement statuses, while the final symbolic error follows that precedence. E5A's JSON gate uses `OUTPUT_FORMAT_UNAVAILABLE`; enabled unimplemented AI uses `CAPABILITY_UNAVAILABLE`.

### 4.3 Commands

Keep `zotwatch config validate` strictly offline and schema/semantic only. Add:

```text
zotwatch runtime preflight --workspace <path> [--json]
zotwatch runtime preflight --workspace <path> --verify-zotero [--json]
```

The default command only checks presence/shape and registry status. `--verify-zotero` performs one bounded, read-only Zotero authentication/library-identity request, never syncs SQLite, and is the only E5 live credential check. AI upstream verification remains `unsupported` until an adapter exists; E5 must not implement an AI call for validation. Public-pool reachability belongs to candidate execution status rather than user credential validity.

Human output uses fixed sanitized messages. JSON output has a version, overall readiness, requirement IDs and the three fields above. It excludes values and lookup names.

## 5. E5B recommendation JSON contract

### 5.1 Path and top-level schema

Publishable artifact path: `reports/recommendations.json`. Schema owner is ZotWatch; first schema version is integer `1`.

```json
{
  "schema_version": 1,
  "run_id": "0199...",
  "generated_at": "2026-09-11T07:00:00Z",
  "count": 2,
  "items": []
}
```

Top-level fields are closed (`additionalProperties=false`). `count` must equal `items.length`; item order is recommendation order and `rank` is contiguous from 1. `generated_at` is one UTC run timestamp, not each writer's wall clock.

Do not serialize `RankedWork.model_dump()` directly. A dedicated projection model explicitly copies allowed fields and rejects unexpected fields.

### 5.2 Item schema and null semantics

Each item contains:

| Field | Type / rule |
| --- | --- |
| `rank` | integer >= 1; position in `items` |
| `work_key` | stable identity described below; never rank-based |
| `source` / `source_identifier` | required non-empty strings; retain source identity |
| `public_id` | string or null; only a public-pool row aid, not canonical identity |
| `doi` | normalized DOI string or null |
| `title` | required string |
| `abstract` | literature-content string or null |
| `authors` | ordered string array; empty means source supplied no usable authors |
| `published_at` | UTC RFC 3339 string or null; missing date stays null |
| `venue` / `url` | string or null |
| `is_preprint` | boolean or null; transported from an explicit adapter/payload field, otherwise null |
| `score` | finite number; legacy score is not promised to be 0–1 |
| `score_breakdown` | closed object for the current components |
| `label` | current deterministic `must_read | consider | ignore` value |

`score_breakdown` has exactly the seven terms in the current legacy formula: `similarity`, `recency`, `citations`, `altmetric`, `journal_quality`, `author_bonus`, and `venue_bonus`. Each is `{raw_value: finite number, weighted_contribution: finite number, input_available: boolean}`. `journal_sjr` remains number or null as supporting public metadata. Missing citation/altmetric/date/SJR/author/venue input is therefore distinguishable from an available input whose score is zero.

The projector obtains raw values from additive ranking provenance. In particular, E5B adds `altmetric_score` because current `RankedWork.metric_score` preserves only the transformed citation value even though total score includes both citation and altmetric terms. The new provenance is observational: the ranker computes the same seven terms once, stores them, and sums the same weighted contributions in the same order. Tests require `score == sum(weighted_contribution)` within the established floating tolerance and require unchanged total scores/order/goldens.

`CandidateWork.is_preprint` is optional and defaults to null. The public-v1 mapper copies its existing `is_preprint` boolean without coercing missing values; explicit arXiv/bioRxiv/medRxiv adapters set true. Other adapters leave it null unless their upstream protocol supplies an explicit reliable value. The JSON projector never infers false from source absence and never uses title/type/URL heuristics. This metadata transport does not change preprint filtering/ranking in E5; the frozen legacy policy continues to use its existing behavior until separately versioned.

No `metrics` or `extra` object is copied wholesale. `public_id` and any future allowed public field require an explicit projector/schema addition.

### 5.3 Stable work identity

`work_key` follows the state/feedback contract:

1. If a valid DOI is present, normalize Unicode/whitespace, remove DOI URL/prefix, lowercase the DOI canonical form, and emit `doi:<canonical-doi>`.
2. Otherwise emit `source:<normalized-source>:<normalized-source-identifier>`. Source is lowercase; identifier is Unicode-normalized and trimmed but retains source-defined case/content.

The mapper rejects empty fallback identity. Within one recommendation document, `work_key` must be unique; a collision is an output-contract failure, never repaired with rank or random suffix.

### 5.4 Public safety boundary

Recommendation JSON may later be selected for Pages publication, so it may include only public bibliographic candidate data and derived recommendation scores. It must never include:

- Zotero library items, raw user/library ID, profile centroid, private authors/venues/tags or library identity fingerprint;
- credentials, env/Secret names, Custom connection ID/base URL or complete model/service config;
- absolute paths, request headers/body, traceback, candidate cache internals, or arbitrary `extra` fields.

Abstract/title/author strings remain untrusted literature data. JSON encoding must preserve them as data, and HTML consumers must escape them. A leakage test walks keys and values using sentinel private/secret/path values, not only a hand-maintained top-level key list.

## 6. Private run manifest contract

### 6.1 Location and purpose

Private operational manifests live under the state root:

```text
state/runs/<run-id>/run-manifest.json
state/runs/latest-attempt.json
```

They are not placed in `reports/` and are never part of the Pages publication whitelist. `latest-attempt.json` is an atomic pointer to the most recently finalized attempt, including failure; successful recommendation publication has a separate pointer under reports.

A config failure produces a manifest when workspace/state paths were safely resolved and the run directory could be created. Parser/path failures before that boundary only guarantee stderr + exit code.

### 6.2 Proposed v1 fields

```json
{
  "schema_version": 1,
  "run_id": "0199...",
  "request_id": null,
  "engine": {
    "version": "2.0.0.dev1",
    "revision": null
  },
  "command": "watch",
  "mode": "incremental",
  "config": {
    "source": "v2",
    "schema_version": 2,
    "fingerprint_sha256": "..."
  },
  "started_at": "...Z",
  "finished_at": "...Z",
  "status": "succeeded",
  "state": {
    "generation_id": "...",
    "library_revision": 132,
    "library_identity_sha256": "..."
  },
  "requested_outputs": ["json", "rss", "html"],
  "publish_requested": false,
  "stages": [],
  "candidate_pool": {},
  "artifacts": [],
  "error": null
}
```

Rules:

- `engine.version` is required. `engine.revision` is nullable because an installed wheel cannot reliably infer a Git commit. P2 may inject its verified `job.workflow_sha` through an engine-owned invocation channel; E5 does not guess or claim workflow identity.
- `config.fingerprint_sha256` hashes a canonical **sanitized execution-semantic projection**. It excludes Custom base URLs, credential material, service records and fields irrelevant to the current implemented Basic route. P2 may separately associate a Git blob SHA without serializing the config.
- `state` values are null until E4 state succeeds. Library identity is already pseudonymous but remains private operational metadata.
- artifact references use state-root/report-root relative paths, media type, byte length, SHA-256 and `publishable` boolean. No absolute path is serialized.
- final manifest stages cannot remain `pending` or `running`.
- top-level `error` is null on succeeded/degraded runs; failed runs contain one symbolic code, sanitized catalog message and opaque diagnostic ID. It never contains exception type, traceback, raw exception string or request/config content.

`profile` manifests contain config/preflight/sync/state stages and no recommendation artifacts. `watch` manifests include the full execution stages.

## 7. Stable stage and status model

Public stage IDs are a finite vocabulary owned by the result schema, not Python function names:

| Stage ID | Meaning |
| --- | --- |
| `config_validation` | path/source detection, parse, schema/semantic checks, effective mapping |
| `credential_preflight` | runtime adapter support and credential readiness |
| `zotero_sync` | E3 acquisition + atomic SQLite commit |
| `computational_state` | E4 reuse/build/validation |
| `candidate_fetch` | public/legacy candidate acquisition and cache fallback |
| `dedupe` | remove items matching personal mirror |
| `ranking` | existing scoring plus current recency/preprint/top policy |
| `output_render` | build and validate all requested output-generation files |
| `zotero_writeback` | legacy optional `--push`; skipped for Basic v2 |
| `output_publish` | switch successful output generation and compatibility aliases |

Stage status enum: `pending | running | succeeded | skipped | degraded | failed`. Overall run status: `succeeded | degraded | failed`.

Finalization invariants:

- overall `succeeded`: every required stage succeeded or was intentionally skipped; no degraded/failed stage;
- overall `degraded`: no required stage failed, at least one allowed fallback/partial stage degraded;
- overall `failed`: one required stage failed; later unstarted stages finalize as skipped with no fabricated timestamps/errors;
- only the failing/degraded stage carries its stage symbolic error; exception class names never enter the contract;
- empty candidates after a complete successful request can be succeeded with count 0;
- public request failure with an allowed fresh-enough stale cache is degraded;
- all candidate acquisition unavailable with no allowed cache is failed, not successful empty output;
- strict mode converts a publishable degraded computation into exit 5 and prevents updating latest-success outputs, while retaining the private degraded manifest.

## 8. Symbolic error and numeric exit policy

Numeric codes stay few and stable:

| Exit | Category | Examples |
| --- | --- | --- |
| `0` | completed | overall succeeded or allowed degraded |
| `2` | configuration/path | invalid/mixed config, unsupported v2 CLI overlay, invalid paths/schema |
| `3` | capability/credential dependency | adapter unavailable, required fixed credential absent/malformed, explicit credential verification failed |
| `4` | runtime execution | sync/state/candidate/ranking/render/publication/writeback/unexpected failure |
| `5` | strict degradation | usable degraded result recorded, but stable successful outputs not updated |

Detailed symbolic codes live in the manifest and machine result. Initial closed catalog:

- config: `CONFIG_INVALID`, `CONFIG_MIXED_MODES`, `CONFIG_OPTION_UNSUPPORTED`;
- preflight: `OUTPUT_FORMAT_UNAVAILABLE`, `CAPABILITY_UNAVAILABLE`, `CREDENTIAL_MISSING`, `CREDENTIAL_MALFORMED`, `CREDENTIAL_VERIFICATION_FAILED`;
- sync/state: `ZOTERO_SYNC_FAILED`, `STATE_INCOMPATIBLE`, `STATE_BUILD_FAILED`, `STATE_CORRUPT`;
- candidates: `CANDIDATE_PARTIAL`, `CANDIDATE_STALE_CACHE`, `CANDIDATE_UNAVAILABLE`, `CANDIDATE_PAYLOAD_INVALID`;
- execution/output: `DEDUPE_FAILED`, `RANKING_FAILED`, `OUTPUT_CONTRACT_INVALID`, `OUTPUT_RENDER_FAILED`, `OUTPUT_PUBLISH_FAILED`, `ZOTERO_WRITEBACK_FAILED`, `INTERNAL_ERROR`.

One central mapper translates known typed boundary exceptions to this catalog. Unknown exceptions map to `INTERNAL_ERROR`; they do not create permanent numeric codes. Sanitized messages are fixed by code and may mention stage/diagnostic ID, never raw remote/config/credential content.

`zotwatch` returns these codes. The legacy module entry point remains available; where exact historical exception behavior is relied on, its wrapper may retain it while still delegating successful work to shared orchestration. P2 must invoke `zotwatch`, not parse legacy traceback behavior.

## 9. Candidate/harvester failure boundary

E5B introduces a `CandidateFetchOutcome` alongside the candidate list:

```text
candidates
status: succeeded | degraded | failed
used_cache
cache_age/fetched_at when safe
requested window_days
request_complete
source outcomes with stable source IDs/status/error code
```

For compatibility, `CandidateFetcher.fetch_all()` continues returning `list[CandidateWork]`. The coordinator uses a new `fetch_with_outcome()` API; both share the same current fetching/cache implementation. This adds observability and terminal all-source failure handling without changing Crossref pagination, source mapping, retention, OpenAlex behavior, direct top-venue compatibility or the public v1 protocol.

`request_complete=true` means only that the engine finished the bounded request under the current v1 protocol and budgets. It does not claim cross-source completeness, freshness beyond observed timestamps, canonical deduplication, or reliable long-term incremental semantics. H1–H3 remain separate.

## 10. Output generation/publication lifecycle

### 10.1 Layout

```text
reports/
  recommendations.json                 # stable compatibility/public path
  feed.xml                             # existing stable RSS path
  report.html                          # new stable HTML path
  report-YYYYMMDD.html                 # existing dated compatibility path
  report-empty.html                    # existing empty compatibility path
  .zotwatch-output/
    latest-success.json                # atomic authoritative pointer
    generations/<run-id>/
      recommendations.json             # if requested
      feed.xml                          # if requested
      report.html                       # if requested
    staging/<run-id>.tmp/
```

The private run manifest stays in `state/runs/`, outside this publishable tree. P2 publishes only files explicitly listed `publishable=true` from the successful output generation; it never copies `.zotwatch-output`, state, or the whole reports directory.

### 10.2 Lifecycle

1. Rank/filter/top completes in memory.
2. Create run-scoped staging under the reports filesystem.
3. Render every requested JSON/RSS/HTML file with one `run_id/generated_at` context. Existing RSS and dated/empty HTML writers retain byte-compatible content for E0 frozen time.
4. Parse/validate JSON schema, XML structure, UTF-8 HTML and artifact allowlist; compute hashes/sizes.
5. If any render/validation fails, delete/ignore staging, write a private failed manifest, and leave current pointer plus all stable files untouched.
6. If legacy Zotero writeback was requested, perform it only after staged output validates and before output publication. A writeback failure does not publish staged reports.
7. Rename the complete staged directory to immutable `generations/<run-id>` on the same filesystem.
8. Atomically replace `latest-success.json`. Only a complete validated generation can become authority.
9. Finalize the immutable generation artifact list with generation-relative path, SHA-256, size, media type and publishable flag. This list plus the atomic `latest-success` pointer is the machine authority consumed by RunResult/run manifest/P2.
10. Reconcile each requested stable compatibility path using temp-file + fsync + `os.replace`. Each visible file is therefore either an old complete artifact or a new complete artifact. An interruption can temporarily leave aliases from different **successful** generations, never partial bytes or a failed render; the next invocation repairs aliases from the authoritative pointer before starting a new run.
11. Finalize the private success manifest and `latest-attempt` pointer.

Unrequested formats are not deleted. P2 uses the current generation's explicit artifact list, so a stale unrequested compatibility file cannot be accidentally published as part of the current run.

Empty recommendations are a valid complete generation and may atomically replace previous outputs when candidate acquisition itself succeeded. A failed/unknown empty acquisition cannot.

This design avoids pretending multi-file paths provide cross-filesystem ACID. The single authoritative pointer is atomic; compatibility aliases are independently atomic and recoverable.

## 11. RunResult API and P2 handoff

Internal orchestration returns:

```text
RunResult
  run_id
  status
  exit_code
  error_code / sanitized_message / diagnostic_id
  manifest_path
  output_generation_id
  recommendation_json_path
  rss_path
  html_path
  state_generation_id
```

Python `Path` objects may be absolute in memory for the caller process. Persisted/machine JSON uses only state/report-root relative references.

Add `--machine-result` to the public `zotwatch` entry point. It writes exactly one closed `run-result-v1` JSON object to stdout after finalization; logs remain on stderr. It contains run/status/exit/error and immutable-generation artifact references, not stable aliases, config, profile data or absolute paths. Each P2-facing reference agrees with the finalized artifact whitelist's generation-relative path/hash/size/media type. Without the flag, the CLI prints concise human status and returns the same numeric code.

P2 will only need to invoke the engine, read this machine result, and upload/restore files named by validated contracts. It must not parse logs, inspect Python exception names, derive success from process 0 alone, or scan directories for newest files.

## 12. Privacy and sanitization rules

Three output classes remain distinct:

| Class | Location | Publication |
| --- | --- | --- |
| Publishable recommendation output | current successful report generation + stable aliases | only when user/P2 explicitly opts in and only allowlisted fields/files |
| Private operational result | `state/runs/<run-id>` | private artifact/status input only |
| Rebuildable computational state | E4 SQLite/generations/cache | private state artifact later; never report/Pages/Git |

Neither public nor private E5 result serializes credential values/names, raw Zotero user ID/item data, Custom endpoint, full service config, absolute path, full environment, request body/header, exception repr or traceback.

Model strings are user-controlled for registered future services. Until AI execution exists, a failed capability manifest records provider category/feature and null model; it does not echo arbitrary model input. Custom is recorded only as provider `custom` plus safe feature/protocol capability if needed, never connection/base URL.

## 13. Backward compatibility

E5 must preserve:

- E0 RSS GUID and complete RSS/HTML goldens;
- legacy dated and empty HTML paths plus `reports/feed.xml`;
- E1 wheel/editable/outside-checkout execution and packaged resources;
- E2 strict schema, registry single source of truth, mixed-mode rejection and offline config validation;
- E3 revision/trash/atomic SQLite semantics and credential slot names;
- E4 state manifest/generation/lease invariants and ranker's validated StateHandle;
- legacy config execution, advanced self-hosted public override and no forced migration;
- Basic mode without any AI credential;
- current scoring formula, ranking order and candidate fetch/direct top-venue behavior except the explicit all-unavailable failure classification.

Intentional E5 changes:

- Basic v2 `profile/watch` become executable;
- unsupported enabled AI fails capability preflight before recommendation side effects;
- requested JSON and private manifests are added;
- public `zotwatch` gains stable exit/error semantics;
- complete empty candidate results are distinguishable from unavailable acquisition;
- failed/degraded-strict runs do not replace latest successful reports;
- v2 config, rather than legacy-only CLI flags, owns Basic top/output choices.

No E0 fixture/golden is rewritten to hide these changes. New JSON/run contracts receive separate E5 fixtures/goldens.

## 14. Expected modified files

Plan-only commit changes only this file. After approval, expected implementation files are:

### E5A

- `zotwatch/runtime/config.py` — `EffectiveRuntimeConfig`, one source detector/adapter and v2 Basic mapping.
- `zotwatch/runtime/preflight.py` — fixed-slot presence/runtime support and optional Zotero live verification.
- `zotwatch/runtime/models.py` — request/preflight typed internal models.
- `zotwatch/cli.py` — public dispatch, preflight command, v2 execution, stable early exits.
- `zotwatch/config/legacy.py` — import shared legacy-v1 policy metadata; no legacy execution projection.
- `zotwatch/providers/registry.py` / `credentials.py` — expose fixed lookup through a non-secret runtime API, without duplicating registry lists.
- `src/cli.py` — extract shared profile/watch coordinator callable while preserving module CLI wrapper.
- `pyproject.toml` — package the new `zotwatch.runtime` package.
- `tests/test_runtime_config_e5.py`, `tests/test_runtime_preflight_e5.py`, `tests/test_runtime_equivalence_e5.py`.
- installed probes, packaging CI and test documentation needed to prove checkout-independent v2 execution.

### E5B

- `zotwatch/results/models.py` — recommendation/run/stage/artifact/RunResult contract models.
- `zotwatch/results/errors.py` — closed symbolic catalog and exception-boundary mapping.
- `zotwatch/results/recorder.py` — stage lifecycle and private manifest finalization.
- `zotwatch/results/publication.py` — output staging, schema validation, generation pointer and alias recovery.
- `zotwatch/resources/recommendations-v1.schema.json`.
- `zotwatch/resources/run-manifest-v1.schema.json`.
- `zotwatch/resources/run-result-v1.schema.json` and optional preflight schema if CLI JSON is public.
- `src/cli.py` / `zotwatch/cli.py` — stage-aware orchestration and machine result output.
- `src/fetch_new.py` — additive `fetch_with_outcome()` metadata; existing `fetch_all()` compatibility wrapper retained.
- `src/rss_writer.py` / `src/report_html.py` — accept shared render context or staging paths without changing legacy rendered bytes.
- `pyproject.toml`, `.gitignore`, packaging checks and E5 docs.
- `tests/test_recommendation_contract_e5.py`, `tests/test_run_manifest_e5.py`, `tests/test_output_publication_e5.py`, `tests/test_exit_codes_e5.py`, plus focused updates to existing CLI/pipeline/install tests.

Exact module names may be adjusted during implementation, but config, runtime, result-contract and writer responsibilities must remain separated.

## 15. Regression / acceptance matrix

| Case | Expected proof |
| --- | --- |
| Legacy successful profile/watch | Existing command/config behavior and E0 ranking/RSS/HTML bytes remain unchanged. |
| Equivalent Basic v2 watch | E5A uses an RSS/HTML-only v2 fixture; with identical mirror/candidates/vectorizer/clock, RankedWork order/scores and RSS/HTML bytes equal legacy default. E5B adds JSON/manifest. |
| E5A JSON request | Schema-valid config fails `OUTPUT_FORMAT_UNAVAILABLE`, exit 3, before sync/model/candidate side effects; no JSON file is created. |
| v2 profile then watch | Both execute E3/E4 rather than return `CONFIG_V2_EXECUTION_DEFERRED`; revision/state handle match. |
| Mixed config | `CONFIG_MIXED_MODES`, exit 2, no sync/fetch/model load. |
| v2 legacy-only CLI overlay | `CONFIG_OPTION_UNSUPPORTED`, exit 2 before side effects. |
| Enabled preset/Custom AI route | Schema remains valid; preflight returns `CAPABILITY_UNAVAILABLE`, exit 3, no Zotero/candidate/AI call. |
| Missing Zotero identity/key | Machine-readable configured=false, verification=not_requested, exit 3; no value/lookup name leaked. |
| Basic no AI key | Preflight ready and profile/watch may run. |
| Explicit Zotero verification | Read-only bounded check; verified/failed distinct from configured; no SQLite mutation. |
| Public pool credential | No caller Supabase requirement; packaged connection used and never serialized. |
| Recommendation JSON golden | Closed schema, deterministic order/ranks/work_key/scores/nulls at frozen time. |
| Complete score provenance | Seven raw/contribution/availability records reproduce total score within tolerance; added altmetric provenance does not change order or totals. |
| Explicit preprint metadata | public-v1 true/false is preserved, explicit preprint adapters set true, absent metadata remains null; projector performs no heuristic inference. |
| Duplicate/missing work identity | Output contract fails; no latest-success replacement. |
| Publishable JSON leakage | Sentinel Zotero item/profile/key/env/base URL/absolute path/extra data absent from all keys/values. |
| Successful profile manifest | Only config/preflight/sync/state stages; no fake recommendation artifacts. |
| Successful watch manifest | All required fields/stages/counts/state generation/artifacts agree. |
| Config failure manifest | Produced when paths are valid; sanitized error, later stages skipped, exit 2. |
| Sync/state failure | Correct stage/error/exit 4; E4 previous generation remains and latest reports unchanged. |
| Candidate complete empty | succeeded, count 0, requested empty outputs may publish. |
| Candidate partial/stale cache | degraded status and symbolic reason; exit 0 normally, exit 5 strict without publication. |
| Candidate unavailable no cache | failed, no successful empty report, previous outputs preserved. |
| Dedupe/ranking failure | Correct stable symbolic code, no new output generation/pointer. |
| JSON/RSS/HTML render failure | Staging ignored/removed; all previous stable aliases and success pointer unchanged. |
| Interruption before generation rename | Staging never selected; recovery ignores/removes it. |
| Interruption after success pointer | Every alias remains complete old/new; next run reconciles aliases from pointer. |
| Legacy Zotero push failure | Validated staging not published; previous outputs preserved. |
| Unrequested format | Previous compatibility file not deleted and current artifact whitelist omits it. |
| Manifest/result sanitization | No secret/env name, raw ID/item, endpoint, traceback or absolute path. |
| Stable exit mapping | Known failures map to 2/3/4/5; detailed distinction remains symbolic. |
| Wheel/editable/outside checkout | v2 Basic execution, schemas, preflight and machine result work from installed engine. |
| P2 artifact references | RunResult/manifest references resolve inside immutable selected generation and match whitelist hash/size/media type; aliases are never authoritative inputs. |
| E3/E4 after E5 failure | SQLite revision/current computational pointer unchanged except a sync already atomically committed before a later-stage failure; stale state is never ranked. |
| Immutable E0 inputs | `git diff --exit-code --diff-filter=DMR v2-e0-baseline -- tests/goldens tests/fixtures` remains empty. |

Tests use frozen Zotero/public fixtures, fixed vectorizer and clock; live credential verification is unit-tested with HTTP fakes. No test contacts an upstream service.

## 16. Implementation commits and PR boundaries

### Plan commit

1. `docs: plan E5 runtime and result contracts` — this file only.

### E5A PR

1. `test(runtime): define E5A v2 execution and preflight regressions` — failing tests plus red evidence against `v2-e4-baseline`.
2. `feat(config): map Basic v2 to effective runtime settings` — one adapter, packaged public connection, shared policy metadata.
3. `feat(runtime): add capability and credential preflight` — offline/default and explicit fake-tested Zotero verification.
4. `feat(cli): execute v2 and legacy through one coordinator` — preserve module CLI, reject unsupported v2 overlays before side effects.
5. `test(runtime): seal E5A equivalence and installation gates` — equivalent legacy/v2 fixtures, wheel/editable/CI/docs.

E5A is merged independently after E0–E4 plus E5A gates pass. Record merge SHA; optional annotated checkpoint `v2-e5a-baseline`.

### E5B PR

1. `test(results): define E5B contract and publication regressions` — failing schemas/stage/error/failure-boundary tests and red evidence on E5A baseline.
2. `feat(results): add recommendation and private run contracts` — versioned schemas/models/projectors/recorder.
3. `feat(runtime): report stages, candidate outcomes, and stable exits` — central coordinator/error mapper/RunResult.
4. `feat(outputs): publish validated output generations safely` — staging, immutable generation, pointer, atomic aliases and recovery.
5. `test(results): seal E5 output, privacy, and installation gates` — goldens, failure injection, packages/docs/CI.

E5B is merged only after E0–E5 gates pass and E0 goldens remain unchanged. Its merge SHA becomes the final E5 baseline and receives annotated tag `v2-e5-baseline`. P2 starts only from that SHA.

No commit combines E5 changes with reusable workflow, template, GitHub artifact transport, AI, candidate/harvester fixes or scoring changes.

## 17. Rollback strategy

Rolling back E5A to `v2-e4-baseline` restores the deliberate v2 execution-deferred behavior while leaving legacy execution and E3/E4 state readable. It writes no new persistent schema that E4 must understand.

Rolling back E5B to E5A leaves E3 SQLite and E4 computational generations untouched. Older engines ignore `state/runs/` and report output-generation directories. Stable legacy `feed.xml` and dated/empty HTML files remain ordinary complete files; an E5A/legacy run may overwrite them using its old writer behavior.

E5 never migrates or deletes durable feedback. Failed/interrupted E5 runs may leave inert staging or immutable unreferenced output generations, which are safe to delete. Rollback never selects them by directory timestamp.

If an E5 result schema must change incompatibly, increment its schema version; do not reinterpret or rewrite old private manifests/recommendation JSON in place. P2 pins the final E5 engine SHA and consumes only declared compatible versions.
