# P2 — Central Reusable Workflow & Thin Workspace Implementation Plan

日期：2026-09-12

状态：**待审阅；只完成 implementation plan，尚未修改 production workflow 或 workspace template。**

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

P2 的完成条件是：两个相互独立、默认 private 的 personal workspaces 仅配置 `ZOTERO_USER_ID` 与 `ZOTERO_API_KEY`，可从空 state 及恢复 state 执行 Basic v2 watch，得到 E5 `run-result-v1`、private manifest、RSS、HTML、recommendation JSON，并证明 state/secret/publication 隔离及 full-SHA 回滚。

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
| `publish-pages` | boolean / `false` | Pages 的第二重显式 opt-in；只有它与 v2 `outputs.publish=true` 同时成立才进入独立 Pages job。 |

P2 v1 不加入 `engine-ref`、任意 command、arbitrary path、public Supabase key、provider endpoint/header、env name 或 Secret name input。Draft 中的 `validate-credentials` / provider selector 暂不进入 P2：E5 只有显式 Zotero verification，所有 AI adapters 仍 unimplemented。以后增加时必须单独版本化，而不是让 workflow 拼任意探测请求。

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

Secrets 只在 engine execution step 通过固定 `env:` 名注入。checkout、identity verification、dependency installation、cache restore、artifact/Pages steps 不接收这些 env，也不 dump contexts。

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
| `publication-url` | Pages 成功才有，否则空 |

Full rebuild + run 会产生两个不同的 engine RunResults/manifests：profile result 保存在 private operational artifact 中，最终 reusable `result-status/run-id` 取 watch RunResult。Profile 失败时不执行 watch，输出 profile failure。不得把 correlation `request-id` 冒充其中任一 `run_id`。

## 4. Exact checkout 与 identity model

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

| Path/job | Repository token permissions | Cache access | 说明 |
| --- | --- | --- | --- |
| Basic compute/validate | `contents: read`，其他 none | `validate`: none；trusted run/profile: write | read 用于 caller/central checkout；无需 contents、PR、workflow 或 administration write。 |
| State restore | 不额外增加 repo write scope | read | 只读 caller repository/ref scoped cache。Miss/error 进入 rebuild。 |
| State save | 不额外增加 repo write scope | write，仅 trusted `schedule/workflow_dispatch` 且 finalized `succeeded` | 使用 GitHub cache service scoped token；低信任 trigger 不允许写。 |
| Private/report artifact upload | `contents: read` | none beyond preceding compute need | `upload-artifact` 使用 run-scoped artifact service；不写 Git。P2A hosted-runner test确认无需扩大 repo scopes。 |
| Optional Pages job | `contents: read`, `pages: write`, `id-token: write` | none | 只在双 opt-in 后运行；权限仅在独立 job。 |

P2A 将按当前 GitHub.com workflow syntax 显式设置 `cache-mode`：compute run/profile 为 `write`，validate 与 Pages 为 `none`。缓存安全文档明确警告不能在 low-trust trigger 打开 write，因此 reusable workflow 还要先校验 event allowlist：<https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#controlling-cache-access-with-cache-mode>。

Default P2B workflow只给 call job `contents: read`，不授予 Pages scopes，且 `publish-pages=false`。README 的 Pages opt-in patch 同时修改 config、input 和 caller permissions；Basic 用户永久不承担发布权限。

## 6. State transport decision record

### 6.1 选项比较

| 方案 | 优点 | 风险/成本 | 决定 |
| --- | --- | --- | --- |
| A. Actions cache | 自动按 caller repo/ref 隔离；prefix 可恢复最近 entry；miss/eviction 天然回退 rebuild；不需 API 查询 previous run | immutable key 设计不当会重现旧 stale bug；不是长期存档；必须防 PR poisoning | **选作唯一自动 state restore transport** |
| B. Previous successful workflow artifact | 明确保留期和可见 checkpoint，可作审计/手动恢复 | 需 Actions API 找 previous successful run/artifact、处理权限/分页/retention；容易把“latest filename”猜测引回 workflow；与 recommendation artifacts 生命周期混杂 | P2 不用作自动 state restore |
| C. Hybrid | 可兼顾 acceleration 和 long-lived debug | 两套 authority、两套 retention/restore/test，当前没有 correctness 收益 | P2 不采用 |

Recommendation/public/private operational outputs 仍用 workflow artifacts；这里只是不把 computational checkpoint 同时复制为 workflow artifact。Cache 永远只是可丢失的 acceleration，不是 durable authority。

### 6.2 Key scheme

Workflow 先从 immutable caller identity计算 `ref_fingerprint = SHA-256(github.ref)`。Cache namespace：

```text
zotwatch-state-v1-<github.repository_id>-<ref_fingerprint>-
```

本轮 primary save key：

```text
<namespace><github.run_id>-<github.run_attempt>
```

Restore 使用一个不可能预先存在的本轮 primary key，并只给同 namespace 的 `restore-keys` prefix。GitHub 在多个 prefix matches 中返回最近创建的 entry：<https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#cache-key-matching>。

每次成功 run 的 key 都唯一，所以不存在 monthly exact hit 后 cache action 拒绝保存更新的问题。不同 repository ID 或 ref fingerprint 永远没有共同 prefix。Cache version 还绑定固定 checkpoint path/compression，但它不替代 bundle schema validation。

只在以下条件全满足时运行 standalone cache save：

- event 是 `schedule` 或 `workflow_dispatch`；
- identity/config/result/checkpoint validations 全部通过；
- final E5 result `status == succeeded` 且 exit code 0；
- checkpoint export 成功；
- 本轮不是 pull request/fork/untrusted dispatch shim。

`degraded` 可以上传已有 private/public results，但不更新 reusable checkpoint。Failed/invocation-crash/Pages-only job 不保存。Cache 被 eviction、restore timeout、下载损坏或没有命中时，删除 restore staging，使用空 state 正常从 Zotero rebuild。

## 7. Bounded checkpoint/state bundle v1

Workflow 不直接缓存 live `state/`。Engine 新增 checkpoint exporter/importer，由它理解 E3/E4 layout：

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
engine_repository
engine_workflow_sha
workspace_repository_id
workspace_ref_sha256
config_fingerprint_sha256
library_identity_sha256            # pseudonymous identity fingerprint
committed_library_revision
state_generation_id
state_manifest_sha256
entries[] = {relative_path, role, sha256, size_bytes}
```

Metadata 不写 raw Zotero ID、item payload、absolute paths 或 secrets。`entries` 是固定 role/path allowlist，有 file-count/individual-size/total-size 上限；import 拒绝 path traversal、absolute path、symlink/hardlink/device、duplicate path、未知 role、hash/size mismatch、future schema、wrong repository/ref namespace。

Exporter 在 official engine run 完成、SQLite 关闭后获取一个短生命周期 E4 state lease，使用 SQLite backup 并读取同一个 current generation，验证 manifest/hash 后才原子发布 bundle staging。它不嵌套 official `watch` 的 run-level lease，因为 watch 已返回并释放 lease。

Importer 将 restored cache 当不可信字节：先在独立 staging 做 schema/path/hash/SQLite integrity/current-generation 验证，成功才装入新的 state root。E3/E4 在 run 中仍进行自己的 library/model/config/revision compatibility validation；checkpoint validation 绝不取代它。任何 mismatch/corrupt/future state 都丢弃整个 restore 并从 Zotero rebuild，不 silent fallback 到 stale generation。

## 8. Concurrency model

Single concurrency owner 在 personal caller workflow，不在 callee 再定义同名 group：

```text
group = zotwatch-<github.repository_id>-<github.workflow>-<github.ref>
cancel-in-progress = false
```

它将同 personal workspace/workflow/ref 的 schedule 与 manual runs 排队，避免同时更新同一个 cache namespace/output stream。不同 repository ID 天然隔离；不同 ref 也隔离。

E4 local file lease继续保护单个 runner/process 的 SQLite → generation → ranking一致性，以及 standalone engine API。GitHub concurrency保护跨 workflow run，不能替代 file lease；file lease也不能协调两个 runners，二者各自保留。

Template 不触发 `pull_request`、`pull_request_target`、fork push 或普通 source push recommendation。若用户以后自行增加低信任 trigger，reusable workflow 的 event guard 会在 cache restore、secret injection 和 engine execution 前拒绝它，而不是依赖 GitHub token 降权后继续。

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

成功或 degraded run 产生两个逻辑 artifact；artifact 名含 caller repository ID、ref fingerprint、engine run ID，retention 天数在 workflow 中显式设置并受 repo policy 上限约束：

- **Publishable recommendation artifact**：只含 materialized `recommendations.json`、`feed.xml`、`report.html` 中 RunResult 实际声明且 `publishable=true` 的文件。上传清单完全来自 RunResult，不额外发明 publishable 文件；原 immutable generation reference 与 hash 保留在 private machine result/manifest 供审计。
- **Private operational artifact**：最终 machine result、其 private run manifest；full rebuild 时再含 profile machine result/manifest；可选 bounded/redacted stderr。它不含 SQLite/FAISS/items/config/secrets。

Checkpoint 只进 caller repo/ref scoped cache，不上传为 artifact。Artifact 上传动作只接收 validator 创建的明确 staging 目录，不接收整个 `reports/`、`state/`、workspace 或 glob wildcard。

### 10.2 Optional Pages

Pages 是独立 conditional job，条件同时要求：

- `publish-pages=true`；
- config 的 `outputs.publish=true`，由 engine-owned sanitized inspection result 确认；
- final watch RunResult 合法并有 publishable artifacts；
- private compute/artifact upload 已经完成。

Pages staging 从 publishable artifact/materialized whitelist 建立，只可含 E5 声明的 RSS/HTML/JSON。禁止 `.zotwatch-output` metadata、run manifest、machine result、state、config、logs 和 credentials。Pages job 使用官方 configure/upload/deploy actions 的 full SHA，且只在该 job 取得 `pages: write`/`id-token: write`。官方 Pages 示例需要这两个权限：<https://docs.github.com/en/get-started/start-your-journey/deploying-your-website-automatically>。

Pages entitlement/policy/deployment 失败不会删除已上传 private recommendation artifact 或已保存成功 state；`publication-url` 保持空并明确标记 publication job failure。P2 不增加“strict publication 导致 recommendation 回滚”的语义。Private repository 也不被描述为 private Pages 保证，README 要求用户理解其 GitHub plan/repository policy。

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

所有第三方Actions（checkout、setup-python、cache restore/save、upload/download-artifact、Pages actions）在production YAML中固定40-char commit SHA，并在注释记录upstream release。`tests/fixtures/p2/action-pins.json`维护action path、SHA、reviewed release/date。Static gate拒绝`@main`、`@vN`和非40-hex引用。升级由Dependabot或人工独立PR提出，审阅upstream changelog/permissions，过central contract与two-workspace canary后生成新P2A SHA，再由template独立PR更新caller SHA；绝不移动旧tag/ref来升级现有用户。

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
- manual inputs `mode`、`full-rebuild` 和 Pages opt-in（默认 false）；
- repository/ref-scoped concurrency 且 `cancel-in-progress:false`；
- `contents: read` default permissions；
- call `run.yml@S_P2A`；
- 显式传两个Zotero secrets。

Manual normal run 使用 `mode=run, full-rebuild=false`。Manual full rebuild 使用 `mode=run, full-rebuild=true`，由 central workflow 依次执行 full profile 再 watch。Profile-only 诊断使用 `mode=profile`。Schedule 走同一个 call job 和 normal run 默认，不把 schedule 放进 central engine。

README onboarding主路径只有：

1. Use template并创建 **private** repository；
2. 添加`ZOTERO_USER_ID`；
3. 添加`ZOTERO_API_KEY`；
4. 启用/手动运行Actions。

不要求Supabase/AI Secret。Advanced区再说明Pages双opt-in、provider fixed slots、full-SHA升级/回滚；不要求普通用户理解service或engine internals。

`.gitignore`覆盖state/reports/checkpoint/env/SQLite WAL/FAISS/embedding/profile/run manifests等private/rebuildable文件，即使用户本地运行也不能被默认`git add .`纳入。Contract gate另外以allowlist确认template Git tree没有这些文件；`.gitignore`本身不是唯一安全边界。

## 13. Security and threat boundary

| Threat | P2 control |
| --- | --- |
| Central repo获得个人Secret | Reusable workflow在caller run/context执行；Secret只存caller repository并逐项传递。Central CI没有个人Zotero credentials。 |
| Caller pin被branch/tag移动 | Template `uses`只接受P2A full SHA；identity assertion比较job SHA与engine HEAD。 |
| Checkout token残留 | 两次checkout均`persist-credentials:false`；无git push。 |
| Arbitrary Secret/env lookup | Workflow declarations与E2 registry exact-match；config不能提供名称；不用`secrets: inherit`。 |
| PR/fork cache poisoning或Secret exfiltration | Template无PR类trigger；callee只接受schedule/dispatch；untrusted path不restore/writecache也不注入Secret。 |
| Workspace代码遮蔽engine package | engine独立venv/install，工作目录和`PYTHONPATH`不含workspace；只把workspace作为data/config path。 |
| Malicious/corrupt restored cache | bundle path/hash/size/schema/namespace validation + SQLite/E3/E4 validation；失败删除并rebuild。 |
| Cross-workspace state leak | cache key含GitHub numeric repository ID；artifacts属于caller repo/run；metadata再次绑定repo/ref。 |
| Private files进入Pages | Pages只消费RunResult-derived publishable staging allowlist；拒绝glob/whole-directory upload。 |
| Secrets进入logs/artifacts | 不printcontexts/env；closed sanitized errors；diagnostic redaction/size limit；privacy tests用canary values扫描全部staging。 |
| Central dependency/action supply-chain drift | engine/workflow/action/model/runtime exact pins与independent upgrade PR。 |

Public candidate pool endpoint/publishable key继续由packaged engine resource处理，不成为caller secret或template文件。Cloudflare完全不在scheduled execution path。

## 14. Expected files

### P2A — ZotWatch

预计新增/修改：

- `.github/workflows/run.yml` — reusable workflow production contract；
- `.github/workflows/packaging.yml` 或独立`.github/workflows/p2-contract.yml` — offline/static gates，不含个人Secret；
- `zotwatch/workflow/identity.py` — repo/SHA/checkout assertion；
- `zotwatch/workflow/checkpoint.py` — bounded export/import与schema validation；
- `zotwatch/workflow/results.py` — E5 RunResult validation/materialization；
- `zotwatch/workflow/__init__.py`与最小CLI wiring；
- `zotwatch/resources/state-checkpoint-v1.schema.json`；
- `constraints/p2-runtime-linux-py311.lock`（最终名称由packaging review确定）；
- `src/vectorizer.py` 与 config/runtime model descriptor 的最小 pin wiring（只解决 audited model revision，不改变算法）；
- `tests/test_workflow_identity_p2.py`；
- `tests/test_workflow_contract_p2.py`；
- `tests/test_state_checkpoint_p2.py`；
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
| Safe config root/blob SHA | exact Git blob accepted；mismatch/symlink/other path rejected before side effects |
| Registry ↔ workflow secrets | exact set match；no broad/unregistered names；no `secrets: inherit` |
| Action refs | every external `uses` is 40-char SHA and in reviewed pins fixture |
| Event/permission lint | no PR trigger; no contents/PR/workflow/admin write incompute; Pages scopes only Pages job |
| Cache namespace | unique save key; same repo/ref prefix restore; A/B and refs cannot collide |
| First restore miss | empty state path proceeds to rebuild |
| Valid checkpoint export/import | SQLite revision and E4 current generation preserved |
| Checkpoint corrupt/stale/future/cross-repo | rejected and clean rebuild selected |
| Path traversal/symlink/oversize/credential canary | import or artifact materialization rejected |
| Bundle bound | exactly SQLite + current pointer/current generation; no runs/cache/history/reports |
| Valid succeeded RunResult | onlydeclared immutable artifacts materialized; hashes/media verified |
| Degraded RunResult | private/report artifact allowed; no state save; no Pages by default |
| Failed/missing/truncated/extra stdout result | invocation failure; no state save/publication |
| Exit/result mismatch | rejected; process 0 alone never implies success |
| Full rebuild orchestration | full profile thennormal watch; distinct results/manifests; watch is final output |
| Page whitelist | onlyRSS/HTML/JSON; no hidden/state/config/private files |
| Secret privacy | canary values absent fromlogs, machine results, manifests, bundles and upload staging |
| Runtime lock/model revision | exact Python/deps/model revision; offline re-install afterprefetch; E4 descriptor matches pin |
| Existing gates | E0–E5 characterization, packaging/config/sync/state/result tests unchanged and green |

Workflow syntax/static tests不能代替GitHub execution。P2A PR还要在一个disposable caller fixture通过full-SHA reusable call验证`job.workflow_*`和cross-repo checkout，再合并。

### 15.2 Two-workspace end-to-end acceptance

建立两个独立private test repositories A/B，都从P2B候选template创建，分别只存自己的Zotero ID/key。真实credentials只在这些private repos；central ZotWatch CI没有environment/repository secrets。

每个workspace执行并记录sanitized证据：

1. **First manual run**：空cache/state，`mode=run`；E3从Zotero建立mirror，E4建立state，final RunResult有效，得到RSS/HTML/JSON与private manifest。
2. **Restored-state run**：再次manual run；cache由同repo/ref最新成功unique key恢复，checkpoint/E3/E4验证通过；新RunResult与输出完整。
3. **Scheduled-equivalent run**：同schedule默认inputs执行同一caller path；至少一个真实schedule trigger完成，或在测试窗口先以相同resolved inputs手动canary并随后记录首个schedule。
4. **Manual full rebuild**：`mode=run, full-rebuild=true`；先有独立profile RunResult/manifest，再有watch RunResult；不由workflow删除state。
5. **State loss**：删除/改变cache namespace或从新ref运行造成restore miss；仍从Zotero成功rebuild。
6. **Corrupt/stale restore**：在private acceptance harness写入可识别的bad checkpoint namespace；import明确拒绝，run重建并不使用stale rank state。
7. **Isolation**：A/B repository IDs、cache namespace、artifact IDs与secrets不同；B无法restore/download A条目，任何metadata identity swap被import拒绝。
8. **Git/public audit**：两个Git trees不含SQLite/FAISS/embedding/profile/run manifest/Zotero items；publishable artifact和可选Pages staging不含state/private files/config/credentials。
9. **Identity audit**：run evidence显示caller SHA、`S_P2A`与checked-out engine HEAD；caller/engine SHA故意不同。
10. **Rollback**：在canary branch把caller的single full SHA改回prior compatible P2A SHA并成功run；cache不兼容时安全rebuild，之后恢复新SHA。绝不使用tag/branch回滚。

验收记录只保存repo/run URL或ID、commit SHAs、status/error codes、artifact names/hashes、cache hit/miss与privacy assertions；不保存Zotero IDs、items、keys、Custom endpoint/key或rawprivate manifests。

## 16. Commit and PR boundaries

### Plan checkpoint（当前）

单独commit只加入`P2_IMPLEMENTATION_PLAN.md`。不修改production workflow/template，等待审阅。

### P2A PR — ZotWatch

建议提交顺序：

1. `test(p2): add failing reusable-workflow and security contracts`
2. `feat(p2): assert workflow identity and pin runtime/model inputs`
3. `feat(p2): export and restore bounded state checkpoints`
4. `feat(p2): validate machine results and materialize artifact whitelists`
5. `ci(p2): add reusable workflow with cache/artifact/pages boundaries`
6. `docs(p2): record P2A gates and callable full SHA`

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
- **State rollback**：checkpoint metadata、E4 compatibility和cache namespace决定能否复用。旧engine遇到future/incompatible checkpoint丢弃并rebuild；不降级best-effort加载。
- **Output rollback**：GitHub artifacts与E5 output generations immutable；失败run不替换authority。Pages失败不删除private result。
- **Transport outage/eviction**：cache miss等同空state；从Zotero重建。Artifact retention到期不影响correctness。
- **Engine invocation crash**：没有合法final RunResult即failure；只上传bounded diagnostics，不save state或publish。

## 18. Explicit non-goals

P2不实现Cloudflare control plane、GitHub App、automatic repo provisioning、Web config editor、Zotero OAuth、AI adapters、clustering/new ranking、durable feedback、harvester H1–H3、automatic central upgrade、arbitrary provider secrets、GitHub artifact-based state discovery或GHES fallback。

P3/P4才处理Web/control-plane/provisioning/upgrades；P2只证明GitHub personal compute/data plane可在Cloudflare离线时独立、安全、可回滚地运行centralized ZotWatch engine。
