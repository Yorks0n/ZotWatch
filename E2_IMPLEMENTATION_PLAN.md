# E2 Implementation Plan — Config v2 & Provider/Credential Registry

日期：2026-09-10

状态：待审阅；本文档不包含 production code 实现。

起点：`codex/v2` / `v2-e1-baseline` / `90b6c949fe18ad485010e6548b6cda1c8badbfa0`。

目标分支：`codex/v2-e2`。

## 1. 范围和实现原则

E2 只在 ZotWatch 仓库建立配置边界。它实现 `zotwatch.yaml` v2 的离线加载、严格 schema/语义校验、provider/credential metadata registry、公共候选池内部连接元数据，以及独立 legacy adapter。E2 不发送 AI 请求，不引入 provider HTTP adapter，不修改同步、候选获取、排序、输出或 state persistence 语义。

四项原则贯穿实现：

1. 普通 Git 配置只描述非敏感选择；credential 的固定映射由 engine 拥有。
2. schema 校验、跨字段语义校验和运行时 capability 可用性是三个明确阶段。
3. legacy loader 保留原行为；legacy → v2 是可检查的 projection，不是原地迁移，也不覆盖用户文件。
4. provider、protocol、capability 和 credential metadata 只有一个手工维护的 registry source；schema 中的枚举由它生成并由测试防漂移。

## 2. 对外数据模型

### 2.1 顶层配置

`ZotWatchConfigV2` 使用严格 Pydantic v2 models；所有 model 都设置 `extra="forbid"`，字符串和数值不做隐式类型转换。顶层结构如下：

| Model | 字段 | E2 约束 |
| --- | --- | --- |
| `ZotWatchConfigV2` | `schema_version`, `zotero`, `candidates`, `ranking`, `embedding`, `ai`, `outputs` | `schema_version` 固定为 `2`；未知字段失败 |
| `ZoteroConfigV2` | `library_type` | 第一版固定 `user`；身份和 key 不进入 config |
| `CandidatesConfigV2` | `provider`, `sources`, `window_days` | provider 固定 `public-api-v1`；sources 非空且去重；window 固定 7 |
| `RankingConfigV2` | `policy`, `top_n`, `max_preprint_ratio` | policy 固定 `legacy-v1`；`top_n` 1–200；ratio 固定 0.3 |
| `EmbeddingConfigV2` | `provider`, `model` | E2 固定本地 embedding 及契约中的模型；不接受 AI credential |
| `AIConfigV2` | `services`, `features` | 默认空 services，全部 feature 关闭 |
| `OutputsConfigV2` | `formats`, `publish` | 严格枚举和去重；本阶段只校验，不改 writer 行为 |

`AIConfigV2.features` 第一版只发布 `rerank` 与 `summary`。`profile_analysis`、`translation` 留作 schema minor version 扩展，本阶段不提前接受未定义字段。

### 2.2 Service 与 feature routing

普通产品仍以 feature 为中心；service ID 是 Web/模板生成的内部引用。schema 支持：

- `PresetServiceConfig`: `provider`, `model`；provider 必须是 registry 中的 preset。
- `CustomServiceConfig`: `provider: custom`, `protocol`, `base_url`, `model`, `connection_id`；`connection_id` 只接受 registry 固定的 `custom-1`、`custom-2`、`custom-3`、`custom-4`。
- `FeatureRouteConfig`: `enabled`, `service`；关闭时禁止 service，开启时必须引用已存在 service。

内部解析结果为不可变的 `ResolvedFeatureRoute`：

```text
feature -> service id -> provider/protocol -> capability -> credential reference
```

它只含 opaque credential reference 和可公开的路由 metadata，不含 credential value。一个 service 可以被多个 feature 引用；Custom 的同一 `connection_id` 也可被多个 service/model 复用。若相同 `connection_id` 在不同 service 中声明了不同 protocol 或 normalized base URL，则以 `CUSTOM_CONNECTION_CONFLICT` 拒绝，避免 key 被错发到另一 endpoint。

`connection_id` 是 personal workspace 内稳定但非用户命名的固定引用，不是任意 UUID。Web 将来只显示用户可理解的连接标签、protocol、provider 类型和 model；它在写配置时分配一个未占用的固定 ID，普通 UI 不展示该 ID。这样 personal GitHub Actions 只凭 workspace config 和 reusable workflow 已声明的四个 slots 即可解析，不依赖 Cloudflare/D1 的额外映射表。

### 2.3 加载结果与错误

统一 loader 返回 `LoadedWorkspaceConfig`：

- `source`: `v2` 或 `legacy`；
- `config`: `ZotWatchConfigV2 | None`；v2 模式总是已验证对象，legacy 仅在 projection 可无损表达时提供等价 view；
- `legacy_settings`: 仅 legacy 模式持有现有 `src.settings.Settings`；
- `legacy_report`: 仅 legacy 模式持有 mapping/compatibility 诊断；
- `public_candidates`: v2 模式解析到 engine-owned connection reference，不暴露到序列化结果。

配置错误统一为 `ConfigError(code, json_pointer, message)`。错误文本不回显 API key、完整 Custom URL、YAML 原值或完整 effective config。首批稳定 code 包括 `CONFIG_SYNTAX`, `CONFIG_SCHEMA`, `CONFIG_MIXED_MODES`, `CONFIG_V2_EXECUTION_DEFERRED`, `UNKNOWN_PROVIDER`, `UNKNOWN_PROTOCOL`, `UNKNOWN_SERVICE`, `CAPABILITY_MISMATCH`, `CUSTOM_URL_INVALID`, `CUSTOM_CONNECTION_CONFLICT` 和 `LEGACY_MAPPING_LOSS`。

## 3. Schema 与加载流程

### 3.1 Canonical schema

代码内的严格 models 定义字段，registry 定义 provider/protocol 枚举；`zotwatch/config/schema.py` 从两者生成 canonical JSON Schema。生成结果作为 `zotwatch/resources/config-v2.schema.json` 随 wheel 发布。测试要求 checked-in artifact 与实时生成结果逐字节一致，避免在多个模块手工维护 provider 列表。

发布 schema 保持 `additionalProperties: false`，并与 architecture contract 的 `schema_version=2` 结构一致。实现时若发现 draft schema 与已批准文字契约冲突，只在 E2 PR 中做最小契约对齐并单列 diff，不顺带扩展 feature/provider 能力。

### 3.2 Loader pipeline

`load_workspace_config(workspace)` 按以下固定顺序工作：

1. 检测 workspace 根的 `zotwatch.yaml` 及 `config/zotero.yaml`、`sources.yaml`、`scoring.yaml`。
2. v2 文件和任一 legacy 文件同时出现时，立即报 `CONFIG_MIXED_MODES`；不设优先级、不合并。
3. v2 模式使用禁止重复 mapping key 的 `yaml.SafeLoader` 变体；不做 `${ENV}` 展开。
4. 先用发布 JSON Schema 校验结构，再构造严格 typed models。
5. 使用 registry 做 service reference、capability、Custom connection 和 URL 语义校验。
6. legacy 模式完全委托 `LegacyConfigAdapter`；不让 v2 defaults 渗入 legacy runtime settings。

v2 YAML 中以下字段无论位于哪个普通配置分支都必须因 unknown/forbidden field 失败：`api_key`, `api_key_env`, `secret`, `secret_name`, `env`, `env_name`, `credential_slot`, `github_secret`, `publishable_key`, `supabase_key`, `engine_ref`。`endpoint` 和 `base_url` 在 candidates/public pool 中也失败；`base_url` 仅允许出现在 Custom service。

Custom URL 的 E2 离线检查要求 HTTPS、合法 hostname/API prefix，拒绝 userinfo、query、fragment、localhost、`.local`、metadata hostname、loopback/private/link-local/reserved 的 IP literal。DNS 解析、redirect 复检和发请求前地址复检属于未来 adapter；E2 不访问网络。

model identifier 只做非空、trim、控制字符和长度检查。未知或不在推荐列表中的模型名不失败；能力来自 preset/protocol metadata，绝不从 provider label、URL 或 model 名猜测。

### 3.3 CLI 边界

E2 将在安装入口增加纯离线的 `zotwatch config validate --workspace ...`，供用户和后续 reusable workflow 使用。它只输出成功状态或脱敏错误，不打印 effective config、Custom endpoint 或 credential metadata。

新安装入口在 dispatch 前检测配置模式：新旧共存时拒绝；legacy-only 的 `zotwatch profile|watch` 原样委托 `src.cli`；v2-only 的 profile/watch 明确返回 `CONFIG_V2_EXECUTION_DEFERRED`，不伪装成缺少旧文件。兼容入口 `python -m src.cli profile|watch` 保持纯 legacy 行为。E2 不把 v2 配置接入 recommendation execution，也不根据 v2 `outputs`/`ranking` 改现有 CLI 参数。这样可以实现并验证配置契约，同时把执行路径切换留给一个有独立 characterization gate 的后续步骤。

## 4. Provider Registry

### 4.1 Registry shape

`zotwatch/providers/registry.py` 是唯一手工维护的 provider/protocol/capability/credential metadata source。核心不可变记录：

```text
ProviderDefinition
  id
  capabilities
  credential_ref
  recommended_models
  implementation_status
  standard_ui_status

ProtocolDefinition
  id
  capabilities
  credential_kind
  implementation_status

CredentialDefinition
  internal_id
  source_kind
  required_fields
```

`recommended_models` 只是可选 UI hint，不是 allowlist。Registry 明确区分三个状态：

1. `registered`：定义存在，因此 schema/semantic validation 可以识别该 provider/protocol。
2. `implementation_status`：`implemented` 或 `unimplemented`，只表示当前 engine revision 是否有通过契约测试的可执行 adapter。
3. `standard_ui_status`：`selectable` 或 `hidden`，表示普通 UI 是否应提供该选项；只有已注册、adapter 已实现且明确批准进入普通产品面的定义才能为 `selectable`。

E2 中所有 AI network adapter 状态保持 `unimplemented`，普通 UI 状态保持 `hidden`。合法的 preset/Custom 配置仍可通过 schema/semantic validation，但 validation success 不等于 feature 当前可执行；运行能力必须另查 registry runtime status，并在未实现时返回脱敏的 `PROVIDER_ADAPTER_UNAVAILABLE`。

### 4.2 首批 preset 和 capability

E2 按已批准契约集中注册以下 ID：

| Preset | 配置 capability | 固定 credential mapping | E2 adapter | 普通 UI |
| --- | --- | --- | --- | --- |
| `voyage` | `native-rerank` | `VOYAGE_API_KEY` | `unimplemented` | `hidden` |
| `dashscope` | `generation` | `DASHSCOPE_API_KEY` | `unimplemented` | `hidden` |
| `openrouter` | `generation` | `OPENROUTER_API_KEY` | `unimplemented` | `hidden` |
| `deepseek` | `generation` | `DEEPSEEK_API_KEY` | `unimplemented` | `hidden` |
| `openai` | `generation` | `OPENAI_API_KEY` | `unimplemented` | `hidden` |
| `anthropic` | `generation` | `ANTHROPIC_API_KEY` | `unimplemented` | `hidden` |

这些 capability 只用于配置兼容性检查，不代表 E2 会调用服务。`summary` 要求 `generation`；`rerank` 接受 `native-rerank` 或契约允许的结构化 `generation`。Embedding 仍固定 local，不从 AI registry 路由。

### 4.3 Custom protocols

Custom 第一版只注册：

- `openai-compatible-generation` -> `generation`
- `anthropic-compatible-generation` -> `generation`

因此 Custom 可配置 summary 或结构化 generation rerank，不能配置 native embedding/native rerank。配置不接受 method、path、headers、body/response template、module、class、plugin、callable 或 executable 字段。

### 4.4 Credential mapping

Preset credential reference 固定映射到上表的 provider-specific slot。Custom 只在 config 保存固定 opaque `connection_id`。Registry 内部使用不可变映射：

```text
custom-1 -> ZOTWATCH_CUSTOM_1_CREDENTIAL
custom-2 -> ZOTWATCH_CUSTOM_2_CREDENTIAL
custom-3 -> ZOTWATCH_CUSTOM_3_CREDENTIAL
custom-4 -> ZOTWATCH_CUSTOM_4_CREDENTIAL
```

这些右侧名称只属于 engine/workflow implementation contract，不进入普通 schema、生成的 config 或普通 Web UI。Resolver 先把已校验的 `connection_id` 转成内部枚举，再以固定 `match`/静态 mapping 选择预声明 slot；不调用 `os.getenv(connection_id)`，不接受任意 slot/env 字符串，也不扫描环境变量。每个 slot 中的结构化记录仍需与 config 的 protocol 和 normalized base URL 一致。personal GitHub Actions 因此可完全离线完成映射，不依赖 Cloudflare/D1。

E2 实现 metadata resolution 和存在性状态，不读取或发送 AI key。credential 缺失不影响整个 config 的合法性；只有未来执行已启用 AI feature 时才需要检查相应 slot。实现中不出现统一 `AI_API_KEY`，错误、`repr`、日志和测试 snapshot 不包含 key 或 Custom endpoint。

## 5. Public candidate connection

v2 ordinary config 只能写：

```yaml
candidates:
  provider: public-api-v1
  sources: [...]
  window_days: 7
```

engine-owned `zotwatch/resources/public-candidates-v1.json` 保存版本化只读 base URL、publishable key、page size 和 timeout。该资源随 engine revision 打包，并由 v2 loader 返回内部 connection reference；普通 config、CLI 输出和 user Secret 均不出现 `SUPABASE_PUBLISHABLE_KEY`。

现有 legacy `sources.yaml.public_api` endpoint/key/env 继续由 legacy adapter 原样加载，明确标记 `advanced_compatibility`。E2 不删除旧 Secret、不把托管 key 发往自定义 endpoint，也不修改 `CandidateFetcher` 或 public API 请求语义。

## 6. Legacy adapter 边界

`zotwatch/config/legacy.py` 是唯一允许读取三个旧文件、展开旧 env placeholder 及接触旧 endpoint/env-name 字段的新模块。它调用现有 `src.settings.load_settings()`，不重写原 Pydantic models，确保 profile/watch 得到与 E0 相同的 `Settings`。

Adapter 还提供纯函数 `project_legacy_to_v2(settings) -> LegacyMappingReport`。报告包含：

- 成功映射的 ordinary v2 字段；
- 只在 legacy runtime 保留的 advanced fields；
- 无法无损表达的字段及原因；
- 可选的 v2 projection；有 blocking loss 时 projection 为 `None`。

映射规则显式版本化：

- 当前标准三文件可映射到 `library_type=user`、public source 集合、`legacy-v1`、local embedding、AI disabled 和默认 outputs。
- Zotero user ID/API key env、public endpoint/key/env、MAILTO、Altmetric credential、direct-fetch 参数不进入 v2 projection。
- 非标准 scoring weights/thresholds/decay、非空 whitelist、非 7 日 window 或 self-hosted/direct-fetch 设置继续在 legacy runtime 工作，但报告为 compatibility-only 或 blocking loss，不假装无损转换。
- Adapter 从不写 `zotwatch.yaml`，不重命名旧文件，也不修改任何 Secret。

检测到新旧配置共存时统一 loader 失败；显式调用 legacy adapter 仍只读取 legacy 文件，供 characterization 和故障诊断使用。不会存在把 v2 的部分值覆盖到 legacy `Settings` 的路径。

## 7. 预计修改文件

计划审阅通过后预计修改：

| 文件 | 作用 |
| --- | --- |
| `zotwatch/config/__init__.py` | 导出 v2 models/loader/error API |
| `zotwatch/config/models.py` | 严格 typed config models |
| `zotwatch/config/schema.py` | JSON Schema 生成、加载与结构校验 |
| `zotwatch/config/loader.py` | mode detection、YAML 加载、校验编排 |
| `zotwatch/config/semantic.py` | service/capability/Custom URL 语义校验 |
| `zotwatch/config/legacy.py` | 原 loader 委托及可测试 projection/report |
| `zotwatch/config/errors.py` | 稳定、脱敏错误类型和 code |
| `zotwatch/providers/__init__.py` | registry 公共只读 API |
| `zotwatch/providers/registry.py` | provider/protocol/capability/credential 单一来源 |
| `zotwatch/providers/credentials.py` | 固定 credential references 与 Custom slot metadata resolver |
| `zotwatch/resources/config-v2.schema.json` | 生成并随包发布的 schema artifact |
| `zotwatch/resources/public-candidates-v1.json` | engine-owned 只读公共池连接配置 |
| `zotwatch/cli.py` | 仅增加离线 config validate dispatch；旧命令继续委托 `src.cli` |
| `pyproject.toml`, `MANIFEST.in` | 新 package/subpackages、JSON resources、必要 schema validator dependency |
| `tests/test_config_v2.py` | schema、defaults、严格字段、混合模式测试 |
| `tests/test_provider_registry.py` | registry 单一来源、capability、credential metadata 测试 |
| `tests/test_legacy_adapter.py` | 原 settings 等价、projection/loss、不写旧配置测试 |
| `tests/test_config_installation.py` | wheel/editable 外部 workspace 的 schema/resource/validate smoke |
| `tests/fixtures/config-v2/*.yaml` | 最小、preset、Custom 与非法配置样例；不修改 E0 fixtures/goldens |
| `README.md`, `README.en.md` | v2 validate 与 legacy compatibility 的最小说明 |

默认不修改 `src/settings.py`、`src/fetch_new.py`、`src/score_rank.py`、`src/cli.py`、现有 `config/*.yaml`、E0 fixtures/goldens、daily workflow 或 harvester。若实现中发现必须触及这些文件，先更新本计划并重新审阅。

## 8. 测试方案

### 8.1 Schema/loader matrix

- 最小 v2 config 无 AI key、无 Supabase key 可解析，AI features 默认关闭。
- 每个 preset 接受推荐模型和自填 model identifier；未知 provider 明确失败。
- 两个 Custom generation protocol 合法；HTTP/private/metadata/userinfo/query/fragment URL 失败。
- summary/rerank 的合法 capability 组合成功；Custom native embedding/rerank、summary→Voyage 等不兼容组合失败。
- feature 引用不存在 service、关闭 feature 携带 service、connection 冲突失败。
- 任意层级出现 env/Secret/key/slot/engine-ref/request-template 字段失败。
- duplicate YAML key、错误 scalar 类型、unknown field、schema version 非 2 失败。
- 同时存在 v2 与任一 legacy 文件失败；单独模式不发生 merge。

### 8.2 Registry/credential tests

- provider/protocol ID 唯一，credential mapping 唯一；`custom-1` 至 `custom-4` 与四个 slots 一一固定映射，其他 ID 失败。
- schema enum/artifact 由 registry 生成且无漂移；其他模块不维护 provider allowlist。
- model 推荐不是 allowlist；能力只来自 provider/protocol。
- registry 的 runtime status 在 E2 全为 `unimplemented`、普通 UI status 全为 `hidden`；不会被 config success 误报为可调用或可选。
- 缺全部 AI credentials 时 config/validate 成功；错误和对象 `repr` 不泄漏 credential/Custom URL。

### 8.3 Legacy compatibility tests

- 对 E0 标准 workspace，adapter 输出的 `Settings.model_dump(by_alias=True)` 与直接 `src.settings.load_settings()` 完全相同。
- 现有 profile/watch characterization 在 adapter 引入后仍产生同一 SQLite/FAISS metadata、ranking、RSS/HTML golden。
- 已知 explicit YAML 优先级、MAILTO、7 日/0.3、scoring 等 E0 行为不修正。
- 非标准 legacy 字段继续运行且 mapping report 明确 loss；旧文件 mtime/content hash 不变。
- E0 `tests/fixtures` 与 `tests/goldens` 相对 `v2-e0-baseline` 无 diff。

### 8.4 Packaging/CI

- 常规 E0 suite 全绿。
- E1 wheel/editable、pip check、源码目录外 workspace smoke 全绿。
- 从 sdist 构建的 wheel 包含 config schema、registry subpackage 与 public pool resource。
- 安装环境中的 `zotwatch config validate` 对最小/preset/Custom 样例工作，不依赖源码 checkout。
- 新测试禁止真实网络；不使用真实 Secret。

## 9. Commit 与 PR 拆分

计划本身先作为独立 docs-only commit 提交到 `codex/v2-e2`，审阅通过前不写 production code。实施后保持一个 E2 PR，按以下可独立审阅的 commits 组织：

1. `feat(config): add strict v2 models and schema loader`

   只加入 models、schema、YAML loader、基础结构测试。
2. `feat(config): add provider and credential registry`

   加入单一 registry、Custom protocols、capability/URL 语义、公共池内部 resource 及测试。
3. `feat(config): isolate legacy adapter and validation entrypoint`

   加入 legacy projection/report、混合模式拒绝和离线 validate；保持旧执行 pipeline。
4. `test(config): verify installed E2 contract and immutable baselines`

   加入 wheel/editable 验收、README 和 CI gate；明确检查 E0 fixtures/goldens 无 diff。

PR 从 `codex/v2-e2` 指向 `codex/v2`。不混入 harvester、workflow/template、AI adapter、算法或 state 变更。合并前要求 E0 characterization、E1 packaging/install 和 E2 config matrix 全部通过；合并后记录完整 SHA，并在版本策略允许时建立 `v2-e2-baseline`。

## 10. 审阅后实施顺序与回滚

先实现纯 models/schema，再实现 registry 和语义校验，随后接入 legacy adapter，最后补安装态验收。每一步先跑针对性测试，再跑完整 E0/E1 gates。任何 CI 差异先定位 Python/package/path 环境，不改 E0 golden expected behavior。

E2 的回滚边界是删除新 `zotwatch.config`/`zotwatch.providers` package、resources 和 validate dispatch；旧 `src.settings` 与 profile/watch pipeline 保持可用。用户的三个 legacy 配置文件和 Secrets 从未被迁移或改写，因此回滚不需要数据恢复。
