# P5C8 — Daily scheduling / metadata / publication dates

**P5C8 STAGING = PASS。** 2026-10-09，用户基于主要功能的真实 staging 验证及当前正常运行情况确认阶段通过，本阶段开发结束。A–D 代码、真实推荐、跨运行持久缓存和自动 RSS 发布均已验证。生产部署保持不变，不代表 Production Readiness PASS。

初始 E2E 证据截止时间：2026-10-09 00:24 Asia/Shanghai / 2026-10-08 16:24 UTC。后续 RSS 排版修复、实际发布和用户阶段验收记录于 2026-10-09（Asia/Shanghai）。

## 实施范围

A–D 均基于 P5C7 `latent-auto-v1`，没有增加推荐算法、期刊权重、quota、LLM、D1、Worker 或新的部署服务。

每日 caller 安装在私有 staging 仓库 `ykson-dev/zotwatch-p5a3-staging-20260929` 的默认分支 `main`，保留既有 `.github/workflows/p5c4-lifecycle.yml` 和 workflow 名称 `ZotWatch per-user staging`，以便原 RSS 的 `workflow_run` listener 自动跟随。每日时间为 UTC 15:23 / Asia/Shanghai 23:23（cron `23 15 * * *`）。最终固定 engine SHA：`37ee8b8f53266a9f46f7003e6d7d180499be8b3f`（首次 E2E 使用 `683dbd084b34acd6c32c22bf5cb6ba57d07deb3c`；最终 pin 仅修正注册日期缺失时的诊断 provenance，推荐逻辑不变），独立分支 `staging/daily-metadata-v1`。

发现旧 staging `watch.yml` 仍有每日定时，因此仅在该私有 staging 仓库移除它的 schedule，保留其历史手动入口。生产 caller 和旧用户 caller 未迁移。临时 checkout 继续选择独立 `zotwatch-latent-auto.yaml`，P2 固定 config-path 仍为 `zotwatch.yaml`。engine 在运行前强制检查 `ranking_policy=latent-auto-v1`、`candidate_policy=center-recall-v1`；默认分支定时不会进入 topic-v1。

[GitHub schedule 文档](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)说明 schedule 运行默认分支，并可能延迟；选择非整点分钟，不能把配置 cron 等同于准点执行保证。

## Monthly/material-change gate

没有新增 monthly workflow。每次正常推荐沿用 `LifecycleStore.ensure`：Zotero sync 后检查当前用户模型，缺失时 bootstrap；未到原检查周期则复用，达到周期且文库没有 material change 仍复用；满足原 gate 才重建。未变化文献的 embeddings 复用，新 generation 验证后才通过既有 CAS 切换 current revision。

P5C4 builder、generation/schema、encoder policy、私有 latent-state archive 与 transport 没有修改。MiniLM revision 保持 `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`，fingerprint 保持 `340252ec18dd3f961b7f234c30857c51c851445705628f2e4200695020e5daa0`。recall threshold 仍为 0.55，最大 cosine 与 primary center 排序、每中心第一轮最多 3 篇、原 preprint admission、top_n=20 不变。

## DOI 摘要补全及持久缓存

在 candidate collection 后、center recall 前执行。只按规范化 DOI 精确匹配，不进行标题模糊检索。已有非空有效摘要原样保留；直接取得的 Crossref metadata 会作为优先来源，无 DOI 候选不新增外部摘要查询。

缺失摘要按 Crossref → Europe PMC → OpenAlex 查询；每个返回记录必须匹配请求 DOI。Europe PMC 使用 core result 的 `abstractText`；OpenAlex 重建 `abstract_inverted_index`。每 DOI 独立捕获服务/元数据错误，最多 4 个并发请求，失败不会阻止其他候选，仍保留现有“缺失摘要不进入编码”的资格规则。

独立文件 `metadata-cache-v1.json` 使用 `zotwatch-metadata-cache / schema_version=1`，绑定 numeric workspace repository ID。记录 DOI、abstract、`abstract_source`、`fetched_at`、`abstract_status`、到期时间、provider attempts、发表日期 provenance。状态为 available / not_found / unavailable。正缓存 30 天；三方确认没有摘要时负缓存 7 天；服务失败只缓存 1 小时，不能冒充确认缺失。Crossref 日期查询失败也使用短缓存。

跨 Actions run 通过独立私有 Git branch `zotwatch-metadata-cache-v1` restore/publish，复用已有 `GitState` 的 API/私有 ownership 验证及 fast-forward CAS 机制。JSON 上限 16 MiB、5000 个 DOI，按确定顺序保留最新条目。相同 tree 不重复提交。它不进入、更不修改 P5C4 的 latent generation、latent schema 或 latent-state archive。缓存 restore/publish 失败不会阻塞推荐。

私有 filter diagnostic 内新增 `metadata_enrichment` 观察记录；推荐主 result 仍是 P5C7 的 versioned latent-auto contract，不添加虚假的 topic/profile evidence。RSS 继续消费最终 recommendations，仅对选中的公开标识补摘要；公开 DOI fallback 顺序与推荐相同，不把私有 cache/model 上传到 Pages。

资料：[Europe PMC REST](https://europepmc.org/RestfulWebService)、[OpenAlex 单条实体](https://help.openalex.org/api/get-single-entities/)、[OpenAlex authentication](https://help.openalex.org/api/authentication/)。OpenAlex 支持基本匿名查询，也支持可选 `OPENALEX_API_KEY`；额度或服务失败按 unavailable 处理。

## Publication date 与精度

Crossref 优先级固定为 `published-online → published → issued → published-print`，采用第一个格式合法的发表日期。`created.date-time` 仅记录为 `doi_created_at`，不参与发表日期 fallback 或七日 admission。公共候选 API 中 Crossref 原有 published_at 也不会绕过 DOI 元数据校正。

保留 day / month / year / missing 精度。高优先级字段只有年或年月时保留其真实精度，不使用低优先级字段补造具体日期，也不虚构发表时刻。缺失、部分精度、非法或未来日期均以明确原因排除。内部 CandidateWork 的 datetime 适配仅承载日期；新 Crossref final recommendation 的 published 输出为真实 YYYY-MM-DD，不声称午夜是发表时刻。

`utc-calendar-seven-days-v1`：以运行当日 UTC 日期 D 为准，接纳 `[D−6, D]` 的 7 个完整日历日期，两端包含。真正带时区的 provider timestamp 先转 UTC，再取日期，与 YYYY-MM-DD 用同一个边界判断。collection 的较宽时间窗口可以保留，最终 admission 使用上述统一规则。

日期依据：[Crossref metadata fields](https://github.com/CrossRef/rest-api-doc/blob/master/api_format.md?plain=1)、[Crossref REST filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)。

## 验收证据

首次真实推荐：[37798282955](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37798282955)，默认分支 caller commit `27b1a05f4656061ec03981bf0f970cad4aa46567`，内部 run ID `fdacc71baf294fb79a58d866d50f2771`。4m45s 完成，result 为 succeeded / LATENT_AUTO_READY。

| 检查 | 首次真实运行 |
|---|---|
| 模型 | 原 revision `549d00e7ed254ed19af521ddafcbee36`，全部 28 centers |
| Monthly gate | `reuse_before_monthly_check`，encoded_items=0 |
| 摘要 | 87 个真实缺失摘要 DOI 成功补全：Europe PMC 34、OpenAlex 53 |
| 独立持久缓存 | 私有 branch 中 609 个 DOI：available 438 / not_found 166 / unavailable 5 |
| Recall / library dedupe / 七日 / final | 149 / 148 / 112 / 20 |
| 日期窗口 | 2026-10-02 至 2026-10-08，UTC 日期级两端包含 |
| 排序/去重 | 实际 final 顺序与两轮策略重放一致，20 个唯一 work identity |
| 日期语义修正 | 全部候选中 26 个 DOI 的 admission 与“以 created 作为发表日期”的判断不同 |

模型 archive SHA256 保持 `1225fce716b2b060c832b46b4ac33c8e6803d81d32db81b38d68e851359af083`，与 P5C7 完全一致。独立 metadata cache SHA256 `2fec2bb80c42378f10e0ff5cac1fc8a17e48e3a53f3206d36f6d5b6946624b3b`。

Europe PMC 的真实补全样例为 `10.1038/s41477-026-02421-6`（Nature Plants，原始摘要缺失，补全后 available）。其发表日期为 2026-10-01，因此仍被正确排除在七日窗口外；补全摘要不会绕过日期 gate。

真实缺失摘要样例包括 `10.1038/s41586-026-11153-8`（Nature，OpenAlex，fetched_at `2026-10-08T15:10:25.161685+00:00`）。最终推荐 `10.3389/fpls.2026.1997114` 的摘要也来自本次 OpenAlex 补全；它在 Crossref 原始候选中没有摘要，仍经过相同 MiniLM 和 0.55 recall gate。

日期修正样例：`10.1038/d41586-026-03141-9` 的 DOI created 是 2026-10-05，published-online 是 2026-10-01，因此不属于七日窗口。`10.1016/j.molp.2026.09.016` 等只提供部分发表日期精度的记录明确以 `publication_date_insufficient_precision` 排除；没有补造日期或时刻。

最终期刊分布：Frontiers in Plant Science 9、The Plant Journal 3、New Phytologist 3；The Plant Cell、Plant Physiology、PNAS、Bioinformatics、Nucleic Acids Research 各 1。所有推荐来自 Crossref。这是固定 cosine/center 策略的实际结果，没有期刊 quota 或加权。最终贡献来自 12 个 latent centers；按既有 formal_center_ids 顺序，C01=2、C02=1、C03=2、C07=2、C08=2、C09=3、C11=2、C13=1、C16=1、C19=1、C23=1、C26=2，其余 16 个 center 本次贡献 0。

自动 RSS run：[37798935445](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37798935445)，event 为 workflow_run，21s 成功；新增 3 篇，累计 30 items，commit `5911ea19f28bcbf92f19a4b38469e57067137a12`，feed SHA256 `c2b12f2a85b5401123b4c1126d18b056d9d38f40d840b3d8b603c30adabf4678`。同一次 publisher 中重复调用 changed=false / new_items=0。实际 Pages feed 读回 SHA256 与发布结果一致，20 条 final 均存在、GUID 唯一，选中论文 `10.3389/fpls.2026.1997114` 的公开 RSS description 与本次补全摘要完全一致。公开 branch tree 仍只有 feed.xml 与 index.html。

跨 Actions run 缓存验证：[37806064797](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37806064797)，最终 engine pin `37ee8b8f53266a9f46f7003e6d7d180499be8b3f`，默认分支 caller commit `2eaaf8e4ed9d16eb9d9336d07a0eaa722c584554`，内部 run ID `06fdbcf780e04b298dfa58417fd49a94`。3m39s 成功。603 个 DOI 命中已持久化的缓存，24 个新 DOI 被解析，cache 增长到 633 条；它们不是对原 603 条的重复查询。

首次补全的 87 个 DOI 中，本次候选 collection 仍包含 85 个，全部 cache_hit=true，available 且 fetched_at 仍为首次的 `2026-10-08T15:10:25.161685+00:00`。首次全部 87 条正缓存仍保留且 fetched_at 未变。样例 `10.3389/fpls.2026.1997114` 从新 runner 恢复 cache，保持 OpenAlex source 和同一原始 fetched_at。由真实、不同 Actions run 验证了持久缓存，未依赖同一进程内缓存。新 cache SHA256 为 `1e0c5f8f6ef3dbc71c76c113ee21b315491e40963a9865acc9e281d4c6ad25b9`。

第二次运行仍使用全部 28 centers、同一 model revision/fingerprint、0.55 threshold，gate=`reuse_before_monthly_check`，encoded_items=0，模型 archive SHA256 完全相同；最终为 20 个唯一 work，排序重放及 UTC 七日窗口验证通过。

第二次自动 RSS run：[37806565777](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37806565777)，event=workflow_run，成功新增 2 条，累计 32 items。commit `e8ac89050ae0547e1ffcab9720ad8e0b562183d3`，feed SHA256 `440310c8ff28d15f7611b400af866d73d913e708b2a6cf1f322bb51224187fd1`。重复调用 changed=false / new_items=0。实际 Pages feed 已读回并核对相同 SHA；本次全部 20 条 final 均存在，前次 30 个 GUID 与 item 内容全部保持，重复 merge 字节不变。订阅地址仍为 [原 feed.xml](https://yorks0n.github.io/zotwatch-rss-staging/feed.xml)。

两次 source run 均为 workflow_dispatch；RSS 两次均由 workflow_run 自动跟随，不能把手动 source run 称为 scheduled run。默认分支每日 cron 已安装并逐字读回、旧 staging schedule 已移除，但 UTC 15:23 的首次新 schedule event 在截止时仍未出现，已等待超过 60 分钟。历史旧 staging workflow 有成功 Scheduled 记录，因此不能归因于仓库未启用 Actions，也不能凭配置声称新定时已经实际执行。GitHub 可延迟或丢弃 scheduled event；本报告不猜测具体原因，保留截至当时实际触发未观测的事实。2026-10-09 用户明确批准基于已有主要功能验证结束本阶段；首次 schedule event 补证不再作为本阶段关闭的前置条件，留作后续正常运行观察，不将手动运行改称定时运行。

| 验收项 | 状态 | 证据 |
|---|---|---|
| Daily scheduling | PASS（用户阶段验收） | 默认 main 安装唯一新 daily caller；正确 latent-auto pin/config 和 guard 已由真实默认分支运行验证。首次 schedule event 未补证的观察记录保留，不阻塞本阶段关闭 |
| Monthly gate | PASS | 既有 monthly/material-change/incremental tests，真实两次均复用、encoded_items=0、archive 完全一致 |
| Abstract enrichment | PASS | 首次 87 个真实缺失摘要成功补全；第二次仍收集的 85 个全部命中持久缓存 |
| Publication date | PASS | created 仅记录为注册元数据；day/month/year/missing 明确区分；UTC 日期级 7 天统一边界；真实 partial/outside 日期排除 |
| Staging E2E / RSS | PASS | 两个默认分支 source run 和对应自动 RSS run 成功；20 唯一推荐，稳定 GUID、真实补全摘要发布、幂等 |
| Regression | PASS | 全部 28 centers、原模型和 encoder revision/fingerprint、0.55、每中心第一轮 3、top_n=20 不变 |

阶段关闭结论：**P5C8 STAGING PASS**。按用户本次明确验收，不再扩大本阶段开发或验证范围；首次 schedule event 观察记录保留。Production rollout、旧用户 caller migration、D1 0009、Web 推荐卡片、LLM、期刊加权及 Production Readiness 不在本次通过声明内。

## 必要测试

新增 6 项必要测试覆盖 UTC 日期边界与精度、created 禁用、精确 DOI provider 匹配、原摘要保留、正负缓存/服务失败及 RSS fallback/幂等。复用现有 P5C7、RSS 和 P5C4 回归，共 42 项通过；注册日期诊断修正后，相关 17 项通过。原 monthly/material-change 两条必要回归确认未到周期复用、无 material change 复用，达到 gate 时只编码变化记录。没有新增故障注入或兼容性矩阵。

原始私有证据保存在 /private/tmp/p5c8-live 和既有私有 Actions artifacts / model/cache branches；报告不公开私有 library/model/cache 内容。

## RSS 排版修复及阶段关闭

2026-10-09 已集中修复并按用户授权发布：HTML/JATS 源码缩进不再造成段内误换行；段落包列表转换成合法 HTML，保留完整列表及斜体/上下标；摘要后分别显示 `Journal:` 与 `Original article:`。两篇真实问题摘要转换前后全文一致（排除空白），标准浏览器完整显示四项列表。相关 RSS/P5C7/P5C8 测试 20 项通过。

[RSS v4 发布 run 37875340178](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37875340178) 成功更新既有 32 条，new_items=0，GUID/title/link/pubDate 保持不变；重复发布 changed=false。publisher commit `da1c6bf4fd14578fbf9b0a1b50e9eff7edf12074`，RSS commit `b297c543c09d0d0b623788d776c081537c4d8f16`。

[Pages 部署 37875395262](https://github.com/Yorks0n/zotwatch-rss-staging/actions/runs/37875395262) 成功，原订阅地址实际读回 SHA256 `3cae551c1ab3e7cb6f63ad6a4ab6e2fdb77debf98fbb1a8bdd74945e1604a27e`，与发布文件一致。此前暂停的 staging RSS workflow 已恢复。私有推荐运行仓库仍为 `ykson-dev/zotwatch-p5a3-staging-20260929`；RSS 目标仍为显式配置的 `Yorks0n/zotwatch-rss-staging`。

按用户阶段验收，P5C8 开发于 2026-10-09 结束，现有 staging 正常运行与自动发布保留。本次收尾仅更新验收记录，不修改运行配置、模型、推荐策略或生产部署，也不新增测试矩阵。
