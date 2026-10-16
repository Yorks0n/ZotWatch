> 历史归档：本文保留对应阶段的计划或验收事实，不代表当前部署状态或待办事项。

# P5C9C Staging Integration

**P5C9C staging E2E 已通过（2026-10-09），不代表 Production Readiness。** 新私有 staging：`Yorks0n/zotwatch-p5c9-staging-20261009`，仅手动运行；RSS 使用已有 `Yorks0n/zotwatch-rss-staging`。Production、原 caller 与正式推荐策略不迁移。

## 实现

推荐引擎照常产生原始 latent-auto Top 20，结果文件不被 AI 覆盖。独立 staging step 在 finalized result 验证后调用 `zotwatch.staging_ai`，写入与原始结果 SHA-256/run_id 绑定的 `rss-ai-v1.json`。RSS publisher 只接受原 Top 20 的有序子集及标题/摘要英文、中译；若 sidecar 身份、字段或次序无效，恢复原始 Top 20。私人上下文、理由、概率、中心和模型不进入 RSS。

判断 provider 默认为 `none`：原 Top 20；`llm`：现有 compatible Prompt；`jev`：原生结构化选择与保守排除。每次只调用选中的判断 provider，不做双模型验证或跨 provider 失败重试。翻译独立开启，继续使用已接受的逻辑、Prompt 和 API 格式。

Jev 仅执行以下全部成立的排除：原生选项为 mismatch_exclude、有效摘要至少 200 字符、该选项概率 ≥0.80、API confidence ≥0.60、领先次高选项 ≥0.20。未达到条件的原生排除转 review 并保留；仅基于贡献质疑的 no_contribution_exclude 也转 review。保留原生建议和概率方便人工复核。这是保守操作门槛，不宣称概率已经校准，不与 cosine 相加、不重排、不补位。版本 `p5c9-jev-conservative-exclusion-v1`；DeepSeek Prompt 未修改。

翻译、LLM 判断、Jev 判断各使用独立成功缓存文件和版本键；GitHub Actions 中翻译与判断分别恢复/保存缓存。翻译单项失败显示英文；任意质量判断失败恢复完整原 Top 20。未配置或两个功能关闭时不生成 sidecar、不读历史兴趣上下文、不调用 AI。

双语 RSS 保留英文题名与摘要，同时显示已成功的中文翻译；保留期刊、DOI link、GUID 和首次 RSS pubDate。累积历史不删除；过滤只影响当前 run 的推荐子集。已有条目可升级为双语，重复同一输入不重复提交。新数据只允许写 feed.xml/index.html。

## 关闭 AI 的 P5C8 兼容性

已使用 saved 真实 P5C8 20 篇，关闭翻译并选择 none：0 API 请求，未生成 sidecar。新 publisher 与封存 P5C8 RSS v4 publisher 对同一输入、元数据、时间生成的 RSS **逐字节一致**，重复发布结果也一致。原始 20 篇顺序不变，译文逻辑及已确认译文 JSON 逐函数/哈希检查不变。

证据：`reports/optional-llm/staging/p5c8-disabled-compatibility.json`。RSS SHA-256：`3bf696344203e149dcdefb0e7da38b5a184bfb4d3964a2acbf0a3ea1e11962dd`。仅 staging sidecar 使用已接受的摘要校验；P5C8 元数据 enrichment、center recall、ranking、跨中心选择和 MiniLM 原样部署。

52 项必要测试通过：现有 P5C9 服务边界、Jev/native cache/失败回退、保守排除与 staging→RSS 投影/幂等、RSS 原回归与 latent-auto 回归。未做大规模 benchmark。

## 配置

私有 workspace 的 `zotwatch-optional-ai.yaml` 参照 `examples/p5c9c-staging-ai.yaml`：translation enabled/service=translator；shadow enabled/provider=none；allow_private_context=false。切换 llm 时设置 provider=llm、service=judge 并显式授权；切换 jev 时设置 provider=jev、model=jev-latest 并显式授权。关闭全部 AI：translation.enabled=false、shadow.provider=none。

API key 使用 Secrets：ZOTERO_USER_ID/ZOTERO_API_KEY、LLM_API_KEY、TYPESAFE_API_KEY。普通连接配置使用 LLM_API_FORMAT/LLM_BASE_URL/LLM_MODEL（及两个可选 feature model）Variables。仅实际 AI step 接收 AI Secrets，不向 RSS step 注入。

## 部署与 E2E

独立 engine 分支 `staging/optional-ai-v1`，固定 SHA `6d07293535c22742d114337c6a26ffee536425fa`；父版本为 P5C8 staging `37ee8b8f53266a9f46f7003e6d7d180499be8b3f`。新 caller 初始提交 `f244b8db3fae5a404cc425c662b3b7df3b373d60`。初始默认 none 已部署。

真实推荐：[37890555376](https://github.com/Yorks0n/zotwatch-p5c9-staging-20261009/actions/runs/37890555376) 成功；自动 RSS：[37891659633](https://github.com/Yorks0n/zotwatch-p5c9-staging-20261009/actions/runs/37891659633) 成功。只运行这一轮真实推荐 E2E，未运行另一个判断模型。本轮新 workspace 自动同步并建立自身 latent model，因此当前 Top 20 来自这次真实推荐，不宣称与先前 saved Top 20 相同。

- 原始 Top 20：20 篇，原始结果未覆盖。
- Jev：15 keep / 3 review / 2 exclude，RSS 当前推荐保留 18 篇；10 次请求、无失败重试，input/output tokens 为 32,308 / 1,635，请求耗时合计 1.297 秒。
- 4 个原生 mismatch_exclude 建议中，#8 PanGBank（概率 0.43、confidence 0.32）及 #13 RGF1（0.55/0.46）被保守规则转 review；实际排除 #17 NoiBic biclustering 方法（0.92/0.90）与 #19 Kalloora 保护管理系统地理学研究（0.90/0.88）。这是 Jev 的兴趣判断，不代表全文贡献或质量结论。
- 18 篇中译全部成功：DeepSeek 仅用于翻译，10 次请求含 1 次必要重试，0 首次 cache hit；未改变已确认翻译 Prompt/清理逻辑。原生 Jev 未单独生成相关性、贡献标签或自由文本解释。
- 翻译、质量判断独立 GitHub 缓存保存成功；成功内容下次按各自版本与输入键复用。本轮不额外执行第二次真实推荐来测缓存；既有本地缓存测试与成功缓存列表作为验证。
- 已发布的 18 篇逐篇核验英文题名/摘要、中译、期刊、公开链接及封闭 RSS 字段；公开 Pages HTTP 200。feed 累积 **37 条**，旧历史不删除；18 是本次推荐数量。RSS workflow 内的第二次发布断言通过，本地对已发布 feed 复算也零新条目且字节完全一致。
- RSS 只发布 feed.xml/index.html，没有私人代表文献、中心 ID、模型、判断或 key。公开输出通过 Secret 值检查；私人质量/译文 evidence 保存在 ignored 本地目录。
- 默认配置已恢复 `none` / allow_private_context=false，独立双语翻译保持开启；恢复提交 `0ca2112bd03147cfd323c8b082c4e39d88ffcd32`。新 caller 仅 manual dispatch，没有设置每日双模型或新的 schedule。

[公开 staging RSS](https://yorks0n.github.io/zotwatch-rss-staging/feed.xml)。本地 [E2E 验证 JSON](../../reports/optional-llm/staging/e2e-verification.json)、[双语译文查看](../../reports/optional-llm/staging/translations.html)、[质量判断查看](../../reports/optional-llm/staging/quality-filter.html)、[原始 latent-auto 结果](../../reports/optional-llm/staging/latent-auto-result-v1.json)。

用户明确授权四项本地 Secrets 的目的仓库及 RSS-only deploy key；Secrets 经加密配置，临时私钥已删除。自动审批先拒绝未明确授权的凭据传输，得到用户确认后完成；后拒绝读取完整私有 Actions 日志，改为只读 artifact/状态/公开 feed 检查完成验证，没有读取该日志或增加权限。

没有修改生产默认分支/caller、启用 Production、调整正式 latent-auto 策略，或启动大规模 benchmark。已发布 engine 基于 P5C8 固定版本，仅包含列出的 staging optional-AI/RSS 文件；此前本地未提交的元数据修改未包含在此次部署中。

摘要标题展示更新：英文摘要使用 `Abstract`，中译使用 `摘要`；复用已有推荐结果重发，无新 AI 调用。RSS run `37892935198` 成功，17 篇有摘要的双语条目已核验；另 1 篇无可信摘要，仅双语标题。当前 staging engine pin `2cccb25cc4de6ea0ec4a218d3865bebd70b090fa`。
