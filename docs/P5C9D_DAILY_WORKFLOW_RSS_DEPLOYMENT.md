# P5C9D — Daily Workflow Consolidation & RSS Deployment

日期：2026-10-09（Asia/Shanghai）。**P5C9D 手动 E2E = PASS。** 每日 schedule 已配置；首次自然 schedule 事件尚待观察，不额外运行 AI 验证定时触发。

## 部署范围

- 唯一日常私有入口：[Yorks0n/zotwatch-p5c9-staging-20261009](https://github.com/Yorks0n/zotwatch-p5c9-staging-20261009)。`.github/workflows/p5c4-lifecycle.yml` 每日 UTC 15:23 / 北京时间 23:23，保留 `workflow_dispatch`。GitHub schedule 可能延迟，首次自然触发尚待观察。
- 计算引擎只更新 `Yorks0n/ZotWatch` 的 `staging/optional-ai-v1`，固定 `9f20e94c33347a11713fdfc08cba1774333d9497`；父版本为 P5C9C `2cccb25cc4de6ea0ec4a218d3865bebd70b090fa`。私有 caller 提交 `53bcc574a8e39b18439e6a5bf33ed1112f873bed`；验收后仅更新 README，提交 `c7f93c8a6fdb8dbed62ba75afb99f89c7c42ad14`，workflow 内容不变。
- 新公开仓库：[Yorks0n/zotwatch-feed](https://github.com/Yorks0n/zotwatch-feed)。首次运行从空仓库开始，未迁移旧 RSS 的 37 条历史。唯一公开发布目标变量为 `RSS_PAGES_REPOSITORY=Yorks0n/zotwatch-feed`；沿用 RSS-only publisher 和写入 deploy key 机制。
- 新 deploy key 仅授权新 feed 仓库，私钥经加密存入私有 workspace 的 `RSS_PAGES_DEPLOY_KEY`，本地临时私钥已删除。旧 `zotwatch-rss-staging` 的 deploy key 已撤销；旧 feed 保留访问，不再由该日常入口更新。
- 用户确认旧 P5C8 `ykson-dev/zotwatch-p5a3-staging-20260929` 已删除，当前登录读取为 404；此处不把 404 单独当作删除证明。另发现旧 `Yorks0n/zotwatch-p3-staging-workspace` 的 schedule，已移除并保留手动入口，提交 `c586b0ca044cbf47061fc502829913278d05fce9`。

原 Zotero sync → latent lifecycle → center recall → latent-auto 排序/跨中心 Top 20 → 可选单一 AI 判断 → 可选翻译 → RSS 流程保持。复用原计算与 RSS 并发控制、私有状态/CAS、持久 DOI 缓存和独立 AI 缓存。Production workflow、引擎主分支及生产环境未修改。

## 配置与数据边界

私有 `zotwatch-optional-ai.yaml` 与 P5C9C 配置逐字一致：

| 功能 | 当前配置 | 切换方式 |
|---|---|---|
| 翻译 | enabled=true，DeepSeek / deepseek-flash，中英双语 | 独立设置 translation.enabled；translator.model_env 使用 LLM_TRANSLATION_MODEL，回退 LLM_MODEL |
| 推荐判断 | enabled=true，provider=none；保留完整原 Top 20 | shadow.provider 互斥选择 none / llm / jev |
| LLM 判断 | 当前不调用 | provider=llm、service=judge；judge.model_env 使用 LLM_SHADOW_MODEL |
| Jev 判断 | 当前不调用 | provider=jev、model=jev-latest 或指定模型；使用 TYPESAFE_API_KEY |
| 私人上下文外发 | allow_private_context=false | 判断启用时必须按明确授权设置 true，仅发送已有必要标题/短摘要 |

连接格式 `LLM_API_FORMAT=openai`，endpoint `LLM_BASE_URL=https://api.deepseek.com`，默认 `LLM_MODEL=deepseek-flash`。API key 留在 Secrets。每次只调用选中的判断 provider，失败不切换其他 provider；翻译仍使用独立配置。缓存分别为 translation-cache-v1、quality-llm-cache-v1、jev-cache-v1，沿用 provider/model/输入/规则版本键及必要重试。

部署前逐字校验 `optional_llm.py`、`jev.py`、`staging_ai.py`、`abstract_quality.py` 和 reusable compute workflow 与 P5C9C 远端一致。DeepSeek Prompt、Jev 原生判断及保守排除条件均未修改。翻译单项失败回退英文，质量判断失败恢复完整原 Top 20；原始结果保持不变，sidecar 只供发布使用。

公开输出只含 feed.xml/index.html、公开论文题名/可信摘要、中译、期刊和链接；不发布 Zotero 文库、代表文献、中心、判断理由、概率、私有模型、缓存或 Secrets。英文摘要标题使用 Abstract，中译使用 摘要。

## 摘要修正与本地检查

将 P5C9A/B 已验证的校验正式放入 `zotwatch.metadata`：清理标记后拒绝作者贡献/利益冲突/出版声明/数据可用性声明等正文或尾部文本、超过 6,000 字符的未验证文本，以及短引用式内容。原候选摘要和既有持久缓存也检查；污染缓存不命中，按 Crossref → Europe PMC → OpenAlex 重取。无法取得可信摘要时保持缺失，不截取正文或生成摘要。

RSS 的独立英文元数据回退同样校验，避免关闭翻译或 sidecar 无效时重新公开污染文本。MiniLM、formal latent centers、0.55 recall threshold、日期规则、排名及跨中心选择代码未修改；摘要数据修正可以影响实际候选文本，不能把数据变化误称为算法变化。

61 项必要测试通过，涵盖污染来源回退、旧缓存重取、缺失摘要、RSS、AI 成功缓存/失败回退、Jev 保守规则及 latent-auto 回归。使用已有真实 20 篇关闭全部 AI：0 请求、不生成 sidecar；有效元数据输入下与封存 P5C8 publisher 输出逐字节一致，重复合并零新增。SHA-256：`c0e914ca50c353d81ebe3ea9d06e35a1beba30894441e7ddf82c8432068850a8`。污染摘要被拒绝是此次明确要求的行为修正。

## 真实 E2E

手动推荐：[37895811244](https://github.com/Yorks0n/zotwatch-p5c9-staging-20261009/actions/runs/37895811244)。仅触发一次完整推荐，provider=none、翻译开启。RSS listener 自动消费该成功 run；其原有第二次发布断言用于验证幂等，不重新推荐或调用 AI。

自动 RSS：[37896228610](https://github.com/Yorks0n/zotwatch-p5c9-staging-20261009/actions/runs/37896228610)，成功。新订阅地址：[feed.xml](https://yorks0n.github.io/zotwatch-feed/feed.xml)，首页：[GitHub Pages](https://yorks0n.github.io/zotwatch-feed/)。Pages 发布源为 `gh-pages /`，实际状态 built；该分支首次推送后 Pages 已自动开启，重复初始化返回 409 已开启，随后只读确认源和状态。

| 验收项 | 实际结果 |
|---|---|
| 原始推荐 / AI 后数量 | 20 / 20；provider=none，0 判断请求，完整保留原顺序 |
| 翻译 | 17 cached + 3 succeeded；20 篇双语标题及摘要 |
| 真实翻译 API | deepseek-flash，2 请求、0 重试、0 失败 |
| 私有状态 | 原 model revision 复用，独立持久 DOI 缓存及翻译缓存保存成功 |
| 新 RSS 历史 | 20 条，首次提交没有父提交，未导入旧 37 条 |
| 公开文件 | 严格仅 feed.xml / index.html |
| Pages HTTP | feed 与首页均 200，响应与公开仓库文件逐字节一致 |
| 重复发布 | RSS workflow 内第二次发布断言成功；本地复算零新增且字节一致，GUID / 首次 pubDate 不变 |
| 失败回退 | 本轮无需回退；必要测试覆盖英文回退、完整原 Top 20 回退及无效 sidecar，未人为制造真实 API 故障 |
| 旧入口 | 旧 P5C8 用户确认删除；可访问的旧 P3 schedule 已移除；旧 RSS key 已撤销 |
| 自然 schedule | 已配置，每日北京时间 23:23；首次自然事件尚待观察 |

首次 RSS commit：`a5224c9e655f96c93326fcd2f86726080a140edb`；feed SHA-256：`e4fda15bc44dd46f4bca416590edcc33b68b27dc0dd81ce671e6ace0aafa3e4e`。逐篇验证公开 sidecar 身份、题名/摘要、Journal/原文链接与 Abstract/摘要标题，扫描公开文件不含本机 API key 值或私人中心/上下文字段。

新增译文抽查 NoiBic、Kalloora 和 Roscoea：NoiBic 的双聚类、RNA-seq、C++17 与数据 DOI 保留；Roscoea 的 PacBio HiFi、12、nad4/rps3、非同义替换及“可能”“假设”限定保留。没有修改翻译 Prompt 或清理逻辑。

真实 Roscoea DOI `10.3389/fpls.2026.1967948` 使用 1,799 字符的有效 Crossref 摘要；本次持久缓存前后该条均已可信，因此不声称本次重新调用来源替换了污染缓存。候选中污染文本的清除和旧污染缓存重取由必要测试覆盖。与 P5C9C 先前原始列表相比，本轮 Roscoea 替换了 From plant pangenome assemblies to operational graph references；实时候选及摘要数据发生变化，而模型 revision 和选择算法未变。provider=none 保留的是本次原始 latent-auto Top 20。

私有本地证据保存在 ignored `reports/optional-llm/daily/`：`verification.json`、`e2e-result-summary.json`、`local-compatibility.json`、原始结果、sidecar、翻译及质量关闭汇总。部署示例为 `examples/p5c9d-daily.yml`。API 使用统计沿用现有请求/缓存计数，未新增 token 测量或比较算法。

没有迁移旧历史或订阅地址，没有新增判断模型、模型比较、大规模 benchmark、期刊权重或配额；没有 Production rollout、D1 或 Web 推荐页面。
