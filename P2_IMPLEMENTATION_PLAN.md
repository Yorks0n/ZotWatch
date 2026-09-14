# P2 — Central Reusable Workflow & Thin Workspace Implementation Plan

日期：2026-09-12

本次修订：2026-09-14

状态：**整体批准；已按审阅意见修订 private artifact transport、Pages 权限域与 central repository visibility；尚未修改 production workflow 或 workspace template。**

Engine 起点：`v2-e5-baseline` / `c439bb9046b8c863b12348de8e90e735017ba7e3`。

涉及仓库：

- `Yorks0n/ZotWatch`
- `Yorks0n/zotwatch-workspace-template`

不涉及 `zotwatch-web` 或 `zotwatch-public-harvester`。当前本地 workspace 只有 ZotWatch checkout；对 `Yorks0n/zotwatch-workspace-template` 的未认证只读探测返回 repository not found，因此 P2B 开始前需取得该 private repository 的 checkout，或在它尚未建立时初始化仓库。本计划不假设其中已有可复用实现。

## 1. 决定与完成定义

P2 分成两个严格顺序的 PR，不让两个仓库通过 branch/tag 浮动引用互相等待：

1. **P2A — Central reusable workflow**：只在 ZotWatch 实现 engine-owned workflow glue、state checkpoint、machine-result materialization、完整 workflow 与 contract gates。合并到 `codex/v2` 后记录完整 merge SHA `S_P2A`，可创建 annotated tag `v2-p2a-baseline`。
2. **P2B — Thin workspace template**：从已经封板的 `S_P2A` 生成 caller，其 `uses:` 字面量固定为 `Yorks0n/ZotWatch/.github/workflows/run.yml@S_P2A`。真实 cross-repo acceptance 通过后合并 template PR，记录 template 完整 SHA `S_TEMPLATE`。

最终再在 ZotWatch 增加只含 sanitized evidence 的 `P2_RESULT.md`，记录 `S_P2A`、`S_TEMPLATE`、Actions run URLs/IDs 和验收结论；该记录提交可标记 `v2-p2-baseline`。P2B caller 仍固定可执行 workflow 的 `S_P2A`，不会因为结果文档提交而漂移。

P2 的完成条件是：两个相互独立、默认 private 的 personal workspaces 仅配置 `ZOTERO_USER_ID` 与 `ZOTERO_API_KEY`，可从空 state 及 private checkpoint artifact 恢复 state 执行 Basic v2 watch，得到 E5 `run-result-v1`、private manifest、RSS、HTML、recommendation JSON，并证明 state/secret/publication 隔离及 full-SHA 回滚。

## 2. 已有实现审计与 P2 边界

E5 已提供以下 machine authority：

- `zotwatch watch|profile --workspace ... --state-dir ... --reports-dir ... --machine-result`；
- closed `zotwatch-run-result` schema v1 与 exit categories `0/2/3/4/5`；
- private `runs/<run-id>.json` manifest；
- output immutable generation `.zotwatch-output/generations/<run-id>/...`；
- artifact reference 中的 relative path、SHA-256、size、media type、publishable；
- `latest-success.json` 只选择成功 output generation，stable report files 只是 aliases；
- E3 committed Zotero revision 与 E4 state manifest/current generation validation。

P2 只在这些边界之外增加 transport 和 GitHub orchestration。它不重新判断 recommendation success、扫描 report 目录、改变 E3/E4 state validity，也不修改 ranking、candidate、AI 或 Zotero sync 行为。

当前 `.github/workflows/daily_watch.yml` 是 1.x self-hosted-in-source flow：使用 floating Actions tags、`contents/pages/id-token` 宽权限、月度固定 exact cache key、直接扫描 `reports/*`，并将源 checkout 同时当 workspace。P2A/P2B 验收后它只保留为 1.x compatibility workflow 或在单独清理 PR 中停用；P2 implementation PR 不把它改写成 caller，以免把中央 contract 与 legacy migration 混在同一 diff。

当前 E5 CLI 与 draft contract 有三点差异，P2 v1 明确处理而不默默假设：

- CLI command 是 `watch` / `profile`，workflow input 的 `mode=run` 映射到 `watch`；
- E5 不接收 caller `request-id`，所以 P2 request ID 只作 GitHub invocation correlation，不替代 engine-generated `run_id`；
- E5 loader 只认 workspace root 的 `zotwatch.yaml`。P2 v1 保留 `config-path` 字段，但只接受规范值 `zotwatch.yaml`；扩展任意 nested config path 需要以后修改 config execution contract。

## 3. Reusable workflow contract

P2A 新增公开可调用的 `.github/workflows/run.yml`，只响应 `workflow_call`。不嵌套另一个跨仓库 reusable workflow；每个执行 engine 的 job 都自己核验 workflow identity。目标平台仅 GitHub.com，因为 `job.workflow_repository` / `job.workflow_sha` 当前不在 GHES 提供。

### 3.1 `workflow_call.inputs`

| Input | 类型 / 默认 | P2 v1 语义 |
| --- | --- | --- |
| `config-path` | string / `zotwatch.yaml` | 必须严格等于 root `zotwatch.yaml`；先检查 regular file、无 symlink，再运行 engine。保留字段方便以后做有版本的扩展。 |
| `mode` | string / `run` | allowlist：`run`、`profile`、`validate`。`run` → E5 `watch`；`profile` → E5 `profile`；`validate` 只执行 offline config validation。其他值 fail closed。 |
| `full-rebuild` | boolean / `false` | `profile` 时执行 `profile --full`；`run` 时先执行独立 `profile --full --machine-result`，成功后再执行普通 `watch --machine-result`；`validate` 时必须 false。绝不由 shell 删除 SQLite/FAISS。 |
| `request-id` | string / empty | 可选 UUID correlation ID；空值由 `${github.run_id}-${github.run_attempt}` 形成 opaque correlation。不会进入 config、credential routing 或 engine run ID。 |
| `expected-config-sha` | string / empty | 可选 caller Git blob SHA。checkout 后、任何 engine/model/network side effect 前，以 `git rev-parse HEAD:zotwatch.yaml` 比较；不符返回 `CONFIG_REVISION_MISMATCH`。 |

`run.yml` 不含 Pages input。P2 v1 不加入 `engine-ref`、任意 command、arbitrary path、public Supabase key、provider endpoint/header、env name 或 Secret name input。Draft 中的 `validate-credentials` / provider selector 暂不进入 P2：E5 只有显式 Zotero verification，所有 AI adapters 仍 unimplemented。以后增加时必须单独版本化，而不是让 workflow 拼任意探测请求。

### 3.2 `workflow_call.secrets`

所有 Secret 逐项静态声明，`required: false` 以允许 credential-free `validate`；`run/profile` 的实际必需性由 E5 preflight 决定：

- `ZOTERO_USER_ID`
- `ZOTERO_API_KEY`
- `VOYAGE_API_KEY`
- `DASHSCOPE_API_KEY`
- `OPENROUTER_API_KEY`
- `DEEPSEEK_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `ZOTWATCH_CUSTOM_1_CREDENTIAL`
- `ZOTWATCH_CUSTOM_2_CREDENTIAL`
- `ZOTWATCH_CUSTOM_3_CREDENTIAL`
- `ZOTWATCH_CUSTOM_4_CREDENTIAL`

此列表必须从 E2 Provider/Credential Registry 的 public read API 测试派生，workflow YAML 是静态 consumer。Contract test 要求 registry 的 `workflow_secret_name` 集合与 reusable workflow 声明完全相等（再加两个固定 Zotero slots），并拒绝 `AI_API_KEY`、`SUPABASE_PUBLISHABLE_KEY`、通配 bundle 或未注册 secret。

Template Basic caller 只显式映射两个 Zotero secrets，绝不使用 `secrets: inherit`。Preset/Custom slots 只有用户启用相应 feature 并明确编辑 caller 后才逐项映射；E2 unimplemented adapter 仍会由 E5 preflight 报 capability unavailable，不会因 Secret 存在而变成 executable。

Secrets 只在 engine execution step 通过固定 `env:` 名注入。checkout、identity verification、dependency installation、prior checkpoint discovery/download、artifact/Pages steps 不接收这些 env，也不 dump contexts。

### 3.3 Outputs

Reusable workflow 输出只含 closed、sanitized 值：

| Output | 来源 |
| --- | --- |
| `result-status` | finalized E5 RunResult 的 `succeeded/degraded/failed`；engine 未产出合法结果时为空 |
| `run-id` | 最终 recommendation `watch` 的 engine run ID；profile-only 时为 profile run ID |
| `request-id` | caller correlation ID |
| `engine-sha` | 已核验的 `job.workflow_sha` |
| `manifest-artifact-id` | private operational artifact 的 GitHub artifact ID；未上传则空 |
| `report-artifact-id` | publishable recommendation artifact 的 GitHub artifact ID；无成功 output 则空 |
| `publish-requested` | engine-owned sanitized config inspection 得出的 `true/false`；只供独立 Pages caller job 做第二重校验 |

Full rebuild + run 会产生两个不同的 engine RunResults/manifests：profile result 保存在 private operational artifact 中，最终 reusable `result-status/run-id` 取 watch RunResult。Profile 失败时不执行 watch，输出 profile failure。不得把 correlation `request-id` 冒充其中任一 `run_id`。

### 3.4 Separate Pages reusable workflow

P2A 如提供 central Pages glue，使用另一个纯部署 contract：`.github/workflows/publish-pages.yml`。它不接收 Zotero/provider secrets，也不执行 engine recommendation。Typed inputs 仅允许：

- `run-id`；
- `manifest-artifact-id`；
- `report-artifact-id`；
- `expected-engine-sha`。

这些值必须直接来自同一个 caller workflow run 中 `run.yml` 的 outputs，不能暴露成 `workflow_dispatch` 自由输入。`publish-pages.yml` 验证两个 artifact ID 都属于当前 `github.repository_id` / `github.run_id`，machine result 与 private manifest 的 run ID 和 engine SHA 匹配，report files 正好等于其中的 publishable immutable whitelist。它输出 `publication-url`；任一 mismatch 都不部署。

## 4. Central visibility、exact checkout 与 identity model

### 4.1 P2 v1 visibility precondition

P2 v1 明确要求 `Yorks0n/ZotWatch` central reusable-workflow/engine repository 保持 **public**。Private personal caller 因此可以按 full SHA 调用 central reusable workflow，并从同一个 public repository checkout exact engine commit，不需要 caller-owned PAT、GitHub App installation token 或其他 cross-repository credential。GitHub 的 reusable workflow access matrix允许 private caller使用public workflow；private central repository则需要另行配置access policy：<https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations#access-to-reusable-workflows>。

P2A merge gate 与 two-workspace smoke 都记录 central repository visibility。若 central ZotWatch 以后改成 private，现有 P2 v1 authentication assumption 即失效，必须暂停升级并单独设计/审阅 repository access、scoped token issuance、outside collaborator visibility 和 checkout authentication。P2 不假设 caller `GITHUB_TOKEN` 能读取任意 private central repository，也不预留 PAT/App-token input。

### 4.2 Checkout and assertion

Caller checkout 必须是：

```yaml
- uses: actions/checkout@<audited-40-char-SHA>
  with:
    repository: ${{ github.repository }}
    ref: ${{ github.sha }}
    path: workspace
    persist-credentials: false
```

Central checkout 必须是：

```yaml
- uses: actions/checkout@<audited-40-char-SHA>
  with:
    repository: ${{ job.workflow_repository }}
    ref: ${{ job.workflow_sha }}
    path: engine
    persist-credentials: false
```

安装或执行任何 engine code 前，一个无第三方依赖的轻量 identity command 校验：

1. expected repository literal 是 `Yorks0n/ZotWatch`，规范化 owner/repo case 后 exact match；
2. `job.workflow_repository` 非空且匹配 allowlist；
3. `job.workflow_sha` 是 40 个十六进制字符；
4. `git -C engine rev-parse HEAD` 与 workflow SHA exact match；
5. checkout 是 Git worktree，目标 commit 存在，且 engine path 与 workspace/state/reports 不重叠。

任一字段缺失或不一致即在 secrets 注入和网络/model side effect 前 fail closed。GitHub 官方 job context 正好定义了这三个 reusable-workflow identity 字段及 self-checkout 用法；P2 不做 GHES fallback：<https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context>。

Template caller 的 `uses:` 必须是字面量 `Yorks0n/ZotWatch/.github/workflows/run.yml@<40-char-S_P2A>`。GitHub 不允许该位置使用 expression，静态 full SHA 同时便于依赖图和审计。P4 的可控升级最终也只是审阅并修改这一行。

## 5. Permissions model

Reusable workflow 不能提升 caller 的 `GITHUB_TOKEN` 权限，因此 template caller 和 callee job 都写明最小权限。GitHub 对 called workflow 的权限只能保持或下调：<https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations>。

| Path/job | Repository token permissions | 说明 |
| --- | --- | --- |
| Default Basic caller + `run.yml` compute/validate | `contents: read`, `actions: read`，其他 none | contents read 用于两个checkout；actions read用于精确查询/下载prior private checkpoint。无需 contents、PR、workflow、administration、Pages或OIDC write。 |
| Prior state discovery/download | `actions: read` | GitHub REST 的list workflow runs、list run artifacts、download artifact均由此读取当前private caller repo。官方REST文档对run/artifact读取要求Actions read：<https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow>、<https://docs.github.com/en/rest/actions/artifacts#download-an-artifact>。 |
| Current checkpoint/report/private artifact upload | `contents: read`, `actions: read` | `upload-artifact`使用run-scoped artifact service，不提交Git。P2A hosted-runner gate确认在该声明下上传成功，不为上传扩大repository write scopes。 |
| Opt-in caller Pages job + `publish-pages.yml` | `contents: read`, `actions: read`, `pages: write`, `id-token: write` | actions read只下载当前run的exact artifact IDs；Pages/OIDC权限只存在于这个单独reusable invocation。 |

`.github/workflows/run.yml` 的YAML不得声明、请求或引用 `pages: write`、`id-token: write`、Pages environment和Pages actions。Default P2B caller也不包含Pages job，因此Pages disabled时整个ordinary recommendation permission graph只有`contents: read`与`actions: read`，不存在conditional skipped publish/OIDC权限。

Pages opt-in 是用户明确编辑caller后新增的第二个job：该job `needs` compute，使用同一个`S_P2A` full SHA调用`publish-pages.yml`，并只把compute outputs传给它。只有这个job声明Pages/OIDC scopes。Basic用户永久不承担发布权限。

## 6. State transport decision record

### 6.1 选项比较

| 方案 | 优点 | 风险/成本 | 决定 |
| --- | --- | --- | --- |
| A. Actions cache | 自动restore方便，适合依赖/model downloads | Cache按branch/tag而非workflow identity共享；某些PR可读base/default branch cache。`profile.sqlite`含personal Zotero metadata，importer只能防poisoning，不能防disclosure | **禁止用于任何Zotero-derived personal state** |
| B. Previous successful private workflow artifact | Artifact属于private caller repo/run；可精确绑定workflow/run/event/conclusion/name/id；有显式retention与expired状态 | 需要bounded REST discovery与actions read；缺失/过期时必须rebuild | **选作唯一automatic computational-state transport** |
| C. Hybrid personal-state transport | 两套restore authority | 增加disclosure面和选择歧义，没有correctness收益 | 不采用 |

GitHub明确建议cache不要保存sensitive information，因为能发起PR的人可能读取base branch cache：<https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#best-practices-for-using-caches-securely>。Actions cache在P2仍可用于不含用户数据的Python dependency/model download acceleration，但cache paths和keys必须由central workflow静态定义，并明确排除workspace、state、reports与checkpoint staging。

### 6.2 Structured prior-run discovery

Checkpoint artifact使用固定versioned name `zotwatch-state-checkpoint-v1`，不是caller input。Restore按以下closed流程执行：

1. 要求caller repository是private、当前event是`schedule`或`workflow_dispatch`、当前ref是branch；不满足时`run/profile`在Secret注入前fail closed，`validate`不做state discovery。
2. 使用当前caller `GITHUB_TOKEN`和literal `https://api.github.com`读取`GET /repos/{github.repository}/actions/runs/{github.run_id}`，验证repository ID、caller workflow ID/path、head branch/ref、current event与GitHub contexts一致。
3. 对这个exact caller workflow ID查询runs，固定当前branch、`status=completed`和单页上限15；最多审阅newest-first的15个candidate，不做第二页或无限历史追溯。候选必须是`schedule`或`workflow_dispatch`、head repository ID等于caller repository ID、workflow ID/path与ref完全一致、且不是current run。Overall workflow conclusion不作为checkpoint有效性的替代判断：compute成功上传checkpoint后，独立Pages job失败不应废掉该checkpoint。
4. 对每个候选exact run分别列出artifacts。若不存在**恰好一个**未过期、名字exact为`zotwatch-state-checkpoint-v1`、artifact workflow-run/repository metadata匹配的条目，则继续检查下一个较旧候选。这样validate-only success会被自然跳过。
5. 以该候选返回的numeric artifact ID精确下载到独立staging。下载action/API不接受caller input的artifact name、ID、repository或run ID。
6. 验证GitHub artifact digest（若API提供），再运行checkpoint importer；importer必须确认`source_result_status == succeeded`以及repository/workflow/ref/run namespace与所选candidate一致。Archive、schema、hash、path、repo/ref/config/library/state validation任一失败时删除该candidate staging并继续检查上限内的下一个较旧candidate。
7. 第一个完整通过closed metadata、artifact digest、checkpoint importer与E3/E4 compatibility validation的checkpoint成为restore source。15个候选耗尽或REST/API失败时返回普通restore miss并从Zotero rebuild，不按mtime/文件名猜测，也不接受caller提供的artifact/run ID。

GitHub REST允许按workflow、branch、event/status筛选runs，并要求Actions read；artifact API可从exact run列出和按ID下载：<https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow>、<https://docs.github.com/en/rest/actions/artifacts#list-workflow-run-artifacts>。

“GitHub run成功”不是单独的checkpoint trust proof。`run.yml`只在final E5 result `status == succeeded`、exit code 0、identity/config/result/checkpoint validation全部通过时上传固定name checkpoint；`degraded`虽可保留recommendation/private result，却不上传checkpoint。Importer还要求`checkpoint.json.source_result_status == succeeded`并匹配selected run。因此failed/degraded compute不能成为state source，而Pages-only failure也不会掩盖已经完整上传并自证成功的checkpoint。若newest candidate没有有效checkpoint，bounded search继续检查较旧candidate。

### 6.3 Current successful checkpoint upload and retention

Engine/watch成功且SQLite关闭后导出validated checkpoint，随后以固定name `zotwatch-state-checkpoint-v1`上传到**当前private caller run**，`retention-days`固定为30（受repository policy上限约束）。Workflow rerun若同run已有该name，只允许在本次succeeded checkpoint完成验证后用upload-artifact的reviewed overwrite语义替换当前run内同名artifact；绝不修改previous run artifact。

Artifact expiration、retention policy缩短、API rate/error、download failure或用户删除artifact都等价于restore miss。State仍是rebuildable acceleration；没有artifact不会改变correctness。Recommendation、private operational与state checkpoint artifacts使用不同固定name/payload boundaries。

## 7. Bounded checkpoint/state bundle v1

Workflow 不直接上传 live `state/`。Engine 新增 checkpoint exporter/importer，由它理解 E3/E4 layout：

```text
checkpoint-v1/
  checkpoint.json
  profile.sqlite
  computational/
    current.json
    generations/<current-generation-id>/
      state-manifest.json
      profile.json
      embeddings.npz
      faiss.index
```

只 transport 下一 run 必需的 rebuildable state：

- E3 `profile.sqlite`，通过 SQLite backup API 在已提交 revision 上生成一致 snapshot；
- E4 `computational/current.json`；
- pointer 唯一引用的 current immutable generation 与其 manifest/artifacts。

明确排除：

- `state/runs/`、`latest-attempt.json` 与旧 private run manifests；
- 非 current 的历史 generations 与 staging/lock/tmp；
- candidate cache（不是正确执行必需项，首版不为有限收益扩大私有 bundle）；
- reports、output generations/aliases、workspace config、logs；
- `.env`、credentials、GitHub tokens、provider Custom records。

因此 restore 不能让旧 `latest-attempt` 冒充当前 run，也不会无界增长。

`checkpoint.json` 是 closed, versioned schema，至少记录：

```text
schema_name = zotwatch-state-checkpoint
schema_version = 1
created_at
source_run_id
source_result_status = succeeded
engine_repository
engine_workflow_sha
workspace_repository_id
caller_workflow_id
caller_workflow_path
caller_event
caller_run_id
caller_run_attempt
workspace_ref_sha256
config_fingerprint_sha256
library_identity_sha256            # pseudonymous identity fingerprint
committed_library_revision
state_generation_id
state_manifest_sha256
entries[] = {relative_path, role, sha256, size_bytes}
```

Metadata 不写 raw Zotero ID、item payload、absolute paths 或 secrets。`entries` 是固定 role/path allowlist，有 file-count/individual-size/total-size 上限；import 拒绝 path traversal、absolute path、symlink/hardlink/device、duplicate path、未知 role、hash/size mismatch、future schema、wrong repository/ref/workflow/run namespace。

Exporter 在 official engine run 完成、SQLite 关闭后获取一个短生命周期 E4 state lease，使用 SQLite backup 并读取同一个 current generation，验证 manifest/hash 后才原子发布 bundle staging。它不嵌套 official `watch` 的 run-level lease，因为 watch 已返回并释放 lease。

Importer 将 downloaded private artifact 当不可信字节：先在独立 staging 做 schema/path/hash/SQLite integrity/current-generation 验证，成功才装入新的 state root。E3/E4 在 run 中仍进行自己的 library/model/config/revision compatibility validation；checkpoint validation 绝不取代它。任何 mismatch/corrupt/future state 都丢弃整个 restore 并从 Zotero rebuild，不 silent fallback 到 stale generation。

## 8. Concurrency model

Single concurrency owner 在 personal caller workflow，不在 callee 再定义同名 group：

```text
group = zotwatch-<github.repository_id>-<github.workflow>-<github.ref>
cancel-in-progress = false
```

它将同 personal workspace/workflow/ref 的 schedule 与 manual runs 排队，避免两个run同时选择previous checkpoint并发布竞争的current checkpoint/output stream。不同 repository ID 天然隔离；不同 ref 也隔离。

E4 local file lease继续保护单个 runner/process 的 SQLite → generation → ranking一致性，以及 standalone engine API。GitHub concurrency保护跨 workflow run，不能替代 file lease；file lease也不能协调两个 runners，二者各自保留。

Template 不触发 `pull_request`、`pull_request_target`、fork push 或普通 source push recommendation。若用户以后自行增加低信任 trigger，reusable workflow 的 event guard 会在 prior-run/artifact discovery、secret injection 和 engine execution 前拒绝它，而不是依赖 GitHub token 降权后继续。

## 9. Machine-result consumption

P2A 使用严格的 stdout/stderr/exit boundary：

1. `--machine-result` stdout 只重定向到 run-scoped temporary JSON file；stderr 单独进入 private diagnostic log，并保留 process exit code。
2. Shell 临时关闭 immediate-exit 仅包围一个 engine command，立即保存 `$?`；不使用 pipeline 吞掉 exit code。
3. Engine-owned validator 以 packaged `run-result-v1`/Pydantic model 验证 JSON 正好一个 object、closed schema、manifest safe path、artifact references、hash/size/media type 及 run status/exit consistency。
4. Validator 读取 RunResult 声明的 private manifest 和 immutable output artifact list；不看 stdout 日志、不 grep traceback、不按 mtime 或目录扫描猜文件。
5. Validator/materializer 只复制 RunResult allowlist 到两个干净 staging roots，并再次比对 hash/size。

Full rebuild + run 分别保存 `profile-machine-result.json` 和 `watch-machine-result.json`；watch 是最终 recommendation authority。Validate mode 输出独立 sanitized validation result，不伪装成 E5 RunResult。

如果 process 在 machine-result finalization 前崩溃、stdout 不是合法 contract、declared manifest 缺失或 exit/result 矛盾，workflow 把它标为 **invocation failure**：不保存 state、不发布 Pages、不猜 success。它可上传仅含 machine stdout 原文件、stderr 和 identity/config hash 的 private diagnostic artifact；上传前做固定大小上限和 credential-pattern redaction，不包含 workspace/state/report 目录。

## 10. Artifact and publication policy

### 10.1 Private Actions artifacts

成功或 degraded run 产生recommendation/private operational artifacts；只有succeeded run再产生state checkpoint。Retention在workflow中显式设置并受repo policy上限约束：

- **Publishable recommendation artifact**：只含 materialized `recommendations.json`、`feed.xml`、`report.html` 中 RunResult 实际声明且 `publishable=true` 的文件。上传清单完全来自 RunResult，不额外发明 publishable 文件；原 immutable generation reference 与 hash 保留在 private machine result/manifest 供审计。
- **Private operational artifact**：最终 machine result、其 private run manifest；full rebuild 时再含 profile machine result/manifest；可选 bounded/redacted stderr。它不含 SQLite/FAISS/items/config/secrets。
- **Private state checkpoint artifact**：固定name `zotwatch-state-checkpoint-v1`；只含第7节的bounded bundle；仅final `succeeded` trusted run上传，retention 30天。它包含personal Zotero-derived state，依赖caller repository为private，绝不作为Pages输入。

三个上传动作只接收各自validator/exporter创建的明确staging目录，不接收整个`reports/`、`state/`、workspace或glob wildcard。Artifact IDs作为current run outputs精确传递；后续不按name扫描当前文件系统。

### 10.2 Optional Pages in a separate permission domain

Default template不包含Pages caller job。用户显式opt-in时同时完成两项reviewable改动：config设置`outputs.publish=true`，caller新增第二个job并以同一`S_P2A`调用`publish-pages.yml`。第二个job的条件还要求compute output `publish-requested=true`、final watch RunResult合法且两个current-run artifact IDs非空。

`publish-pages.yml`只用`actions: read`下载**当前caller run**的exact manifest/report artifact IDs。它重新验证machine result/private manifest声明的publishable immutable whitelist，再建立只含E5 RSS/HTML/JSON的Pages staging。禁止`.zotwatch-output` metadata、state checkpoint、run manifest、machine result、state、config、logs和credentials进入Pages payload。

Pages reusable使用官方configure/upload/deploy actions的full SHA，且其caller/callee job才取得`pages: write`/`id-token: write`。纯compute `run.yml`不含这些scopes或actions。官方Pages示例需要这两个deployment权限：<https://docs.github.com/en/get-started/start-your-journey/deploying-your-website-automatically>。

Pages entitlement/policy/deployment失败不会删除已上传private recommendation/operational/checkpoint artifacts；`publication-url`保持空并明确标记deployment failure。P2不增加“strict publication导致recommendation回滚”的语义。Private repository也不被描述为private Pages保证，README要求用户理解其GitHub plan/repository policy。

## 11. Runtime and dependency reproducibility audit

Engine/workflow full SHA 只固定源代码，当前仍有两个 gap：

1. `pyproject.toml`生产依赖只有lower bounds；现有packaging CI依靠`tests/requirements.txt`约束大部分依赖，但sentence-transformers/torch没有production lock。
2. `TextVectorizer`可从local cache读取resolved Hugging Face revision，却调用`SentenceTransformer(model_name)`时没有把immutable revision传给loader；空cache时model `main`仍可漂移。

P2A采用限定到GitHub-hosted `ubuntu-22.04` + CPython `3.11.11`的最小 reproducibility方案：

- setup-python 使用 exact `3.11.11`；
- 新增 engine-owned P2 runtime lock/constraints，固定 direct 和重要 transitive 版本（含 CPU torch、sentence-transformers、transformers/huggingface-hub、numpy/faiss），从相同 engine checkout 安装；
- 安装 engine 时不再让 pip 重新解析 unbounded runtime tree，并保留 `pip check`/resolved inventory evidence；
- 给默认 local embedding contract 固定经审阅的 Hugging Face immutable commit，loader 显式传 `revision`，E4 descriptor 记录同一 revision/artifact identity；
- pin 选择必须与当前模型 artifact 一致，先跑 E0–E5 gates 和 real-model smoke，不能通过改 golden 掩盖输出变化；
- runner image 固定 `ubuntu-22.04`，不使用 `ubuntu-latest`。

不承诺不同CPU/BLAS的bit-identical float结果；E4 hard compatibility依赖stable model/artifact/ABI语义，不hash raw float32 probe。P2只封住明显dependency/model漂移，不引入新包管理系统。

所有第三方Actions（checkout、setup-python、upload/download-artifact、Pages actions，以及如启用的non-personal dependency/model cache action）在production YAML中固定40-char commit SHA，并在注释记录upstream release。`tests/fixtures/p2/action-pins.json`维护action path、SHA、reviewed release/date。Static gate拒绝`@main`、`@vN`和非40-hex引用。升级由Dependabot或人工独立PR提出，审阅upstream changelog/permissions，过central contract与two-workspace canary后生成新P2A SHA，再由template独立PR更新caller SHA；绝不移动旧tag/ref来升级现有用户。

## 12. Thin workspace template contract

P2B 目标内容保持为：

```text
my-zotwatch/
  zotwatch.yaml
  .github/workflows/watch.yml
  README.md
  .gitignore
```

如 GitHub template metadata 确有需要，只增加非业务 metadata；不复制 Python source、provider registry、public pool key、central workflow 逻辑或 tests harness。

Default `zotwatch.yaml` 是Basic schema v2：

- Zotero user library；
- `public-api-v1` candidate provider与契约允许的默认sources/window；
- local `sentence-transformers/all-MiniLM-L6-v2`；
- `legacy-v1` ranking；
- rerank/summary disabled，services为空；
- formats `rss`, `html`, `json`；
- `publish: false`。

`.github/workflows/watch.yml` 只负责：

- `schedule` 和 `workflow_dispatch` triggers；
- manual inputs `mode`、`full-rebuild`；
- repository/ref-scoped concurrency 且 `cancel-in-progress:false`；
- `contents: read`, `actions: read` default permissions；
- call `run.yml@S_P2A`；
- 显式传两个Zotero secrets。

Manual normal run 使用 `mode=run, full-rebuild=false`。Manual full rebuild 使用 `mode=run, full-rebuild=true`，由 central workflow 依次执行 full profile 再 watch。Profile-only 诊断使用 `mode=profile`。Schedule 走同一个 call job 和 normal run 默认，不把 schedule 放进 central engine。

README onboarding主路径只有：

1. Use template并创建 **private** repository；
2. 添加`ZOTERO_USER_ID`；
3. 添加`ZOTERO_API_KEY`；
4. 启用/手动运行Actions。

不要求Supabase/AI Secret。Advanced区再说明provider fixed slots、full-SHA升级/回滚；不要求普通用户理解service或engine internals。Pages说明提供一个明确的opt-in patch：修改`outputs.publish=true`并新增第二个caller job，以同一`S_P2A`调用`publish-pages.yml`，只在该job授予`contents: read`, `actions: read`, `pages: write`, `id-token: write`。Default template本身不含该job或这些权限。

`.gitignore`覆盖state/reports/checkpoint/env/SQLite WAL/FAISS/embedding/profile/run manifests等private/rebuildable文件，即使用户本地运行也不能被默认`git add .`纳入。Contract gate另外以allowlist确认template Git tree没有这些文件；`.gitignore`本身不是唯一安全边界。

## 13. Security and threat boundary

| Threat | P2 control |
| --- | --- |
| Central repo获得个人Secret | Reusable workflow在caller run/context执行；Secret只存caller repository并逐项传递。Central CI没有个人Zotero credentials。 |
| Central repo以后变private | P2 v1以public visibility为显式precondition，不带PAT/App-token fallback；visibility变更必须重新审阅cross-repo access/authentication。 |
| Caller pin被branch/tag移动 | Template `uses`只接受P2A full SHA；identity assertion比较job SHA与engine HEAD。 |
| Checkout token残留 | 两次checkout均`persist-credentials:false`；无git push。 |
| Arbitrary Secret/env lookup | Workflow declarations与E2 registry exact-match；config不能提供名称；不用`secrets: inherit`。 |
| PR/fork读取personal mirror或Secret exfiltration | Zotero-derived state不进入cache；template无PR类trigger；callee只接受schedule/dispatch；untrusted path不查询/download private artifact也不注入Secret。 |
| Workspace代码遮蔽engine package | engine独立venv/install，工作目录和`PYTHONPATH`不含workspace；只把workspace作为data/config path。 |
| Wrong/malicious/corrupt restored artifact | REST先绑定caller workflow/ref/trusted successful exact run和fixed name/ID；bundle path/hash/size/schema/namespace validation + SQLite/E3/E4 validation；失败删除并rebuild。 |
| Cross-workspace state leak | Artifact属于private caller repo/run；REST metadata与checkpoint再次绑定numeric repository ID、workflow、ref和run；只有caller `actions: read`。 |
| Private artifact被repository成员读取 | Checkpoint不是加密vault；其可见性跟随private repository的Actions access。Onboarding明确要求private repo并提醒只授予可信成员read access；public caller的run/profile在上传前fail closed。 |
| Private files进入Pages | Pages只消费RunResult-derived publishable staging allowlist；拒绝glob/whole-directory upload。 |
| Compute获得publish/OIDC权限 | `run.yml`和default caller不声明Pages/OIDC；只有用户新增的second caller job可调用独立`publish-pages.yml`。 |
| Secrets进入logs/artifacts | 不printcontexts/env；closed sanitized errors；diagnostic redaction/size limit；privacy tests用canary values扫描全部staging。 |
| Central dependency/action supply-chain drift | engine/workflow/action/model/runtime exact pins与independent upgrade PR。 |

Public candidate pool endpoint/publishable key继续由packaged engine resource处理，不成为caller secret或template文件。Cloudflare完全不在scheduled execution path。

## 14. Expected files

### P2A — ZotWatch

预计新增/修改：

- `.github/workflows/run.yml` — reusable workflow production contract；
- `.github/workflows/publish-pages.yml` — separate optional Pages reusable contract；
- `.github/workflows/packaging.yml` 或独立`.github/workflows/p2-contract.yml` — offline/static gates，不含个人Secret；
- `zotwatch/workflow/identity.py` — repo/SHA/checkout assertion；
- `zotwatch/workflow/checkpoint.py` — bounded export/import与schema validation；
- `zotwatch/workflow/github_artifacts.py` — fixed GitHub.com prior-run/artifact discovery与metadata validation；
- `zotwatch/workflow/results.py` — E5 RunResult validation/materialization；
- `zotwatch/workflow/__init__.py`与最小CLI wiring；
- `zotwatch/resources/state-checkpoint-v1.schema.json`；
- `constraints/p2-runtime-linux-py311.lock`（最终名称由packaging review确定）；
- `src/vectorizer.py` 与 config/runtime model descriptor 的最小 pin wiring（只解决 audited model revision，不改变算法）；
- `tests/test_workflow_identity_p2.py`；
- `tests/test_workflow_contract_p2.py`；
- `tests/test_state_checkpoint_p2.py`；
- `tests/test_github_artifact_transport_p2.py`；
- `tests/test_workflow_results_p2.py`；
- `tests/test_workflow_security_p2.py`；
- `tests/fixtures/p2/`中的RunResult/checkpoint/action-pin/static caller fixtures；
- `docs/P2_REUSABLE_WORKFLOW.md`与最终`P2A_RESULT.md`。

具体模块可在保持边界的前提下合并，不能为workflow glue建立第二套state/result model。

### P2B — zotwatch-workspace-template

- `zotwatch.yaml`
- `.github/workflows/watch.yml`
- `README.md`
- `.gitignore`

Template验证工具优先留在central P2 contract tests与private acceptance harness，不把Python业务/validation source复制进template。

## 15. Test matrix

### 15.1 Central offline/unit/static gates

| Case | Expected proof |
| --- | --- |
| Correct job repo/full SHA/HEAD | identity passes and emits same SHA |
| Missing/malformed SHA, wrong repo, wrong HEAD | fail closed before install/secrets/network |
| Caller and engine SHAs deliberately differ | engine executes job workflow SHA checkout |
| Central visibility | public `Yorks0n/ZotWatch` recorded; no PAT/App-token input or private-repo fallback |
| Safe config root/blob SHA | exact Git blob accepted；mismatch/symlink/other path rejected before side effects |
| Registry ↔ workflow secrets | exact set match；no broad/unregistered names；no `secrets: inherit` |
| Action refs | every external `uses` is 40-char SHA and in reviewed pins fixture |
| Compute permission lint | default caller/`run.yml` only contents/actions read; no PR trigger, Pages/OIDC or write scopes |
| Pages permission lint | Pages scopes/actions exist only in separate `publish-pages.yml` and explicit second caller job fixture |
| Prior-run selection | exact caller workflow/repository/ref; newest-first最多15个completed trusted candidates；首个自身checkpoint contract有效者 |
| Prior-run rejection | failed/degraded compute marker、PR、wrong repo/workflow/ref/current run不能供state；validate-only自然跳过；Pages-only failure不废弃valid checkpoint |
| Artifact selection | exact selected run + fixed name + one unexpired artifact + numeric ID; no caller-supplied selector |
| Artifact/API miss | no prior run, missing/expired/duplicate artifact, pagination bound or API/download failure rebuilds |
| No personal cache | no cache path can contain profile.sqlite/computational/checkpoint/workspace/reports |
| First restore miss | empty state path proceeds to rebuild and uploads checkpoint only after succeeded result |
| Valid checkpoint export/import | SQLite revision and E4 current generation preserved |
| Checkpoint corrupt/stale/future/cross-repo | rejected and clean rebuild selected |
| Path traversal/symlink/oversize/credential canary | import or artifact materialization rejected |
| Bundle bound | exactly SQLite + current pointer/current generation; no runs/cache/history/reports |
| Valid succeeded RunResult | only declared immutable artifacts materialized; hashes/media verified; checkpoint uploaded with fixed retention |
| Degraded RunResult | private/report artifact allowed; no checkpoint upload; cannot become restore source |
| Failed/missing/truncated/extra stdout result | invocation failure; no state save/publication |
| Exit/result mismatch | rejected; process 0 alone never implies success |
| Full rebuild orchestration | full profile then normal watch; distinct results/manifests; watch is final output |
| Page whitelist | only RSS/HTML/JSON from exact current-run artifacts; no hidden/state/config/private files |
| Secret privacy | canary values absent from logs, machine results, manifests, bundles and upload staging |
| Runtime lock/model revision | exact Python/deps/model revision; offline re-install after prefetch; E4 descriptor matches pin |
| Existing gates | E0–E5 characterization, packaging/config/sync/state/result tests unchanged and green |

Workflow syntax/static tests不能代替GitHub execution。P2A PR还要在一个disposable caller fixture通过full-SHA reusable call验证`job.workflow_*`和cross-repo checkout，再合并。

### 15.2 Two-workspace end-to-end acceptance

建立两个独立private test repositories A/B，都从P2B候选template创建，分别只存自己的Zotero ID/key。真实credentials只在这些private repos；central ZotWatch CI没有environment/repository secrets。

每个workspace执行并记录sanitized证据：

1. **First manual run**：没有previous checkpoint artifact，`mode=run`；E3从Zotero建立mirror，E4建立state，final RunResult有效，得到RSS/HTML/JSON、private manifest与固定name private checkpoint artifact。
2. **Restored-state run**：再次manual run；REST按同repo/caller workflow/ref newest-first检查最多15个completed trusted candidates，按exact artifact ID恢复第一个完整有效checkpoint，importer/E3/E4验证通过；新RunResult与输出完整。插入一个validate-only success后仍能恢复更早checkpoint。
3. **Scheduled-equivalent run**：同schedule默认inputs执行同一caller path；至少一个真实schedule trigger完成，或在测试窗口先以相同resolved inputs手动canary并随后记录首个schedule。
4. **Manual full rebuild**：`mode=run, full-rebuild=true`；先有独立profile RunResult/manifest，再有watch RunResult；不由workflow删除state。
5. **State loss/expiration**：删除checkpoint artifact、用expired/missing/API-failure fixture或从新ref运行造成restore miss；仍从Zotero成功rebuild。
6. **Corrupt/stale restore**：在private acceptance harness让previous trusted run上传可识别的bad checkpoint artifact；import明确拒绝，run重建并不使用stale rank state。
7. **Isolation**：A/B repository IDs、workflow/run/artifact IDs与secrets不同；B的`actions: read`无法查询/下载A条目，任何metadata identity swap被import拒绝。
8. **Git/public audit**：两个Git trees不含SQLite/FAISS/embedding/profile/run manifest/Zotero items；publishable artifact和可选Pages staging不含state/private files/config/credentials。
9. **Identity audit**：run evidence显示caller SHA、`S_P2A`与checked-out engine HEAD；caller/engine SHA故意不同。
10. **Pages permission split**：default run的job/token graph无Pages/OIDC；在acceptance branch显式新增second Pages caller job后，只发布exact current-run whitelist，compute artifact/state已先保留。
11. **Rollback**：在canary branch把caller的single full SHA改回prior compatible P2A SHA并成功run；previous checkpoint不兼容时安全rebuild，之后恢复新SHA。绝不使用tag/branch回滚。

验收记录只保存repo/run URL或ID、commit SHAs、status/error codes、artifact IDs/names/hashes、restore hit/miss reason与privacy assertions；不保存Zotero IDs、items、keys、Custom endpoint/key或rawprivate manifests。

## 16. Commit and PR boundaries

### Plan checkpoint（当前）

Plan-only commits只修改`P2_IMPLEMENTATION_PLAN.md`。不修改production workflow/template；本次transport/permission/visibility修订封板后才建立P2A implementation branch。

### P2A PR — ZotWatch

建议提交顺序：

1. `test(p2): add failing workflow security and checkpoint contracts`
2. `feat(p2): assert workflow identity and pin runtime/model inputs`
3. `feat(p2): export and import bounded state checkpoints`
4. `feat(p2): restore prior successful artifact and upload successful checkpoints`
5. `feat(p2): validate machine results and materialize artifact whitelists`
6. `ci(p2): add pure compute reusable workflow`
7. `ci(p2): add separate optional Pages reusable workflow`
8. `test(p2): complete offline static and integration gates`
9. `test(p2): record disposable cross-repository caller smoke`
10. `docs(p2): record P2A gates and callable full SHA`

各commit只在ZotWatch。P2A merge前运行E0–E5 full gates、P2 offline/static/integration gates、disposable cross-repo caller smoke。合并后以merge commit full SHA作为`S_P2A`，可打annotated `v2-p2a-baseline`。此时workflow已可独立调用，但还没有正式template pin。

### P2B PR — workspace-template

建议提交顺序：

1. `feat(template): add Basic v2 config and full-SHA caller`
2. `docs(template): add private four-step onboarding and state exclusions`

P2B只在template仓库，caller第一版就写已合并的`S_P2A`，绝不暂用`codex/v2`、`main`或tag。PR先过central template fixture/static validation，再从候选commit创建A/B private repos完成上述真实E2E。合并记录`S_TEMPLATE`。

最后只提交sanitized P2 result记录，不趁机修改workflow。任何P2A执行缺陷先在ZotWatch新PR修复并得到新full SHA，再让P2B更新一次literal pin；不force-move已审阅commit/tag。

## 17. Rollback and failure policy

- **P2A rollback**：personal caller把`uses` literal改回已知compatible workflow full SHA。旧central commit永久可审计；不移动tag。
- **P2B rollback**：revert template caller SHA/config commit。已经创建的personal repos不会被template自动改写。
- **State rollback**：bounded candidate run metadata、checkpoint自身metadata与E4 compatibility共同决定能否复用。某个candidate缺失/无效时继续检查15-run上限内更旧candidate；旧engine遇到future/incompatible checkpoint丢弃该candidate，耗尽上限后rebuild，不降级best-effort加载。
- **Output rollback**：GitHub artifacts与E5 output generations immutable；失败run不替换authority。Pages失败不删除private result。
- **Transport outage/retention**：previous artifact缺失、过期、被删除或Actions API/download失败等同空state；从Zotero重建。30天retention只影响加速，不影响correctness。
- **Engine invocation crash**：没有合法final RunResult即failure；只上传bounded diagnostics，不save state或publish。

## 18. Explicit non-goals

P2不实现Cloudflare control plane、GitHub App、automatic repo provisioning、Web config editor、Zotero OAuth、AI adapters、clustering/new ranking、durable feedback、harvester H1–H3、automatic central upgrade、arbitrary provider secrets、cross-repository state storage或GHES fallback。

P3/P4才处理Web/control-plane/provisioning/upgrades；P2只证明GitHub personal compute/data plane可在Cloudflare离线时独立、安全、可回滚地运行centralized ZotWatch engine。
