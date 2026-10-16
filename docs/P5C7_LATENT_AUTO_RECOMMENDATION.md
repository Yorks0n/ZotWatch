# P5C7 — Latent Auto Recommendation

2026-10-08（Asia/Shanghai）。**P5C7 LATENT AUTO STAGING = PASS**。完成真实自动推荐、独立结果持久化及现有 RSS 消费；不代表 Production Readiness PASS。

## 实现

新推荐 policy 为 `latent-auto-v1`，在正常 `watch` 中执行 verified Zotero preflight → sync → 原 P5C4 per-user lifecycle `ensure` → own-model runtime → candidate collection → center recall → library dedupe → seven-day admission → latent ranking/selection → preprint admission → top 20 → immutable private result。初次缺失自动 bootstrap；已存在时沿用 monthly/material-change gate、builder、stable IDs、private generations 与 remote CAS，没有另建模型持久化系统。

新 runner 不调用 confirmed profile loader/readiness、suggestions、topic encoder/ranker，也不读取 feedback branch、active/muted、description、priority、horizon 或 preferences。没有 topic fallback。无 profile 或 all-muted 不构成 not_ready；verified library、model/encoder 等实际依赖不可用才产生相应 not_ready。收集失败/部分失败仍为 failed/degraded，不发布。

`CenterRecallIndex` 原实现对全部 formal centers 计算 cosine；每篇取最大值及确定性的 primary center（cosine 相同按 center ID）。固定 MiniLM revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`，沿用 accepted 128-token windows / equal mean / final L2 policy，fingerprint `340252ec18dd3f961b7f234c30857c51c851445705628f2e4200695020e5daa0`，threshold **0.55**。不累加 secondary scores，不执行 confirmed-topic ≥0.35，不改 embedding/clustering/formation/recall 参数。

全局排序键为 `(-center_cosine, work_key)`。第一轮每个 primary center 最多 3 篇；其余按原 cosine 顺序补足。该确定性两轮顺序交给原 prefix preprint cap，然后截取 top_n=20；因此 preprint 被拒绝时仍可从后续合格论文补足，最终 prefix 和比例仍符合原规则。既有 work identity 去重与 first-occurrence recall 不变，score 等于 center_cosine，无 metadata/venue/author bonus。

## 独立结果与 RSS

新 closed contract：`zotwatch-latent-auto-run-result / schema_version=1`。新状态文件为 `runs/latent-auto-<run_id>.json`，operational artifact 内为 `final/latent-auto-result-v1.json`，envelope 为 `latent-auto-workflow-envelope-v1.json`。包含 run ID、repository/library identity、interest_model_revision、encoder fingerprint、recall policy/threshold、实际全部 formal center IDs，以及每条推荐的 work identity、primary_center_id、center_cosine/score、source/identifier/DOI/venue/published。没有 fabricated confirmed_profile_revision、feedback_commit_sha、topic UUID 或 topic-v1 evidence。

复用独立 `final/latent-deployment-evidence-v1.json`，materializer 校验 exact recorded bytes、exit status、scope、model revision/fingerprint 与 descriptor checksum。历史 v2/v3 parser 保持可识别；新的 per-user staging workflow 只接受 latent-auto 配置，不生成 topic results/suggestions。

现有 RSS publisher 识别新 result/envelope，核对 exact source run、private operational artifact SHA256、repository/run identity、deployment checksum/revision、score/center、唯一 work identity 与 top 20。继续只投影最终 recommendations 的公开论文信息；稳定 DOI/arXiv GUID、公开摘要读取、旧 Feed 合并、首次发布时间和重复发布幂等不变。

订阅地址保持 [feed.xml](https://yorks0n.github.io/zotwatch-rss-staging/feed.xml)，公开仓库仍只发布 `feed.xml` 与 `index.html`。没有 Web 推荐卡片、D1、KV、Worker RSS API 或新部署服务。

## Staging 安装与边界

使用既有私有 workspace `ykson-dev/zotwatch-p5a3-staging-20260929`（ID 1394109116）、既有真实 Zotero 账号及已经持久化的 own-user 28-center 模型。新增 `zotwatch-latent-auto.yaml`，保留原 `zotwatch.yaml` 与 `watch.yml`。手动 `p5c4-lifecycle.yml` 使用正常 run；独立 RSS workflow 自动消费它的 successful workflow_run。

首次启动 [37766556816](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37766556816) 在 P2 的固定 config-path gate 被拒绝，未运行 Zotero/model/recommendation，也没有发布 RSS。修正为只在临时 staging checkout 将独立配置复制到固定 `zotwatch.yaml` 路径；P2 合约与 repository 中的旧配置保持原样。修正后的 engine pin 为 `6013ef7e4339db0aaf15bdfb39841e447f08d555`（独立 `staging/latent-auto-v1` branch），caller head `9c60bf5919eb529d466460055962e8efa91197ee`。

实际推荐 E2E：[37767027633](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37767027633)。Success，6m 48s，attempt 1；internal run `84489005735a49fca92abb3caae6c839`。未执行 profile/Confirm 前置步骤，也未读取 confirmed profile；真实账号已有历史 profile，但此次无任何 profile 读取代码路径。无 profile 与 all-muted 的自动 bootstrap/reuse 由针对性测试直接证明。

必要本地测试：84 passed，覆盖新无 profile/all-muted 自动 bootstrap/reuse、不读取人工配置、max cosine、不累加 secondary、deterministic 两轮/补足/identity 去重、新合约/recorded bytes/sidecar/envelope/RSS 消费，以及受影响的 config、lifecycle 和历史 result parser。没有扩大故障注入/兼容矩阵；补充 offline workflow YAML 与 fixed-path config inspect PASS。

不执行 production rollout、旧用户 caller migration、D1 0009、Worker/Web 部署、Confirmed Interests UI 改造、LLM/semantic naming、embedding/clustering 或 threshold tuning。真实原始 evidence 仅保存在 `/private/tmp/p5c7-live` 与既有私有 Actions/model branches。

## 单次真实 E2E 核对

| 项目 | 证据 / 结果 |
| --- | --- |
| own-user model | revision `549d00e7ed254ed19af521ddafcbee36`；28 个 formal centers 的真实 ID 集合与独立恢复模型完全一致 |
| lifecycle gate | `reuse_before_monthly_check`；encoded_items=0；本次不重建，保持此前 1,596 paper inputs / 1,260 eligible |
| encoder / recall | accepted MiniLM revision / fingerprint 和 0.55 阈值完全一致 |
| isolated final contract | operational artifact `11546565462`，SHA256 `2aaaf985491e15cf4d4db749a956df2c3ad34193cba6f688f31eeb27edbd4f50`；新 result + envelope + exact deployment sidecar，无 topic-result |
| private filter evidence | artifact `11547220177`，SHA256 `e4447af2fe7b4ec8a1f92e97be7388f485df51bca114fed8e32978ae4fb2aac9`；run ID 一致 |
| result verification | parser / sidecar checksum / run / scope / engine pin 校验通过；20 个 unique works；每篇 score 等于 recorded best-match cosine；primary center 一致 |
| deterministic selection | 从此次 saved candidates / recall decisions 重放两轮选择和原 prefix preprint cap，20 条最终 work identity 与顺序完全一致 |
| seven-day admission | cutoff `2026-10-01T11:05:21.529027Z`，即北京时间 10-01 19:05:21.529；所有 final candidate.published 均不早于 cutoff |
| durable private model | 原 CAS publish commit `a3e6cb59d742c431f5cdbdf14d2b15c0c453ff5f`；从 remote branch 独立下载、unpack、校验 generation/descriptor/model；descriptor 与 exact result sidecar 相同 |
| no model mutation | before/after model archive 字节相同，SHA256 `1225fce716b2b060c832b46b4ac33c8e6803d81d32db81b38d68e851359af083`；current model/descriptor/28 IDs 不变 |

最终独立结果仅包含 20 条 recommendations 与必要 latent evidence，不包含逐候选诊断；详细阶段诊断在独立私有 artifact。RSS 消费无需模型 archive、feedback 或文库。

| 阶段（unique work identities） | 全部 | Bioinformatics | PNAS | 植物期刊 | 其他 |
| --- | ---: | ---: | ---: | ---: | ---: |
| recall 输入 | 1002 | 22 | 93 | 162 | 725 |
| center recall | 119 | 17 | 14 | 80 | 8 |
| library dedupe | 118 | 17 | 14 | 79 | 8 |
| seven-day admission | 102 | 12 | 14 | 71 | 5 |
| preprint admission | 102 | 12 | 14 | 71 | 5 |
| final top 20 | 20 | 1 | 1 | 18 | 0 |

此前 P5C5 run 的 74 个 recalled 植物期刊 work identities 全部出现在此次输入，全部仍 recalled，其中 **15 个进入 final**；原 topic-v1 final 中植物期刊为 0。本次另有 3 篇新植物期刊 final。其余旧 recalled 候选可被原 library dedupe、时间窗口或 top 20 淘汰；没有 topic ≥0.35 排除阶段。输入采集与时间不同，因此本报告比较 exact identity 连续性，不将两个 run 的总量差异全部归因于 ranking。

## 各中心贡献

C01–C28 是此次按实际 center ID 排序的匿名别名；完整 ID 映射仅保存在私有 evidence。所有 28 个中心参与完整 cosine 矩阵；0 贡献表示此次没有 primary-match/admission/final，不表示未匹配。

| Center | recalled primary | dedupe/window/preprint 后合格 | final |
| --- | ---: | ---: | ---: |
| C01 | 17 | 17 | 3 |
| C02 | 3 | 3 | 1 |
| C03 | 5 | 5 | 2 |
| C04 | 11 | 7 | 1 |
| C05 | 0 | 0 | 0 |
| C06 | 6 | 6 | 0 |
| C07 | 5 | 5 | 2 |
| C08 | 5 | 3 | 2 |
| C09 | 7 | 6 | 3 |
| C10 | 0 | 0 | 0 |
| C11 | 12 | 9 | 1 |
| C12 | 1 | 0 | 0 |
| C13 | 1 | 1 | 1 |
| C14 | 1 | 1 | 0 |
| C15 | 0 | 0 | 0 |
| C16 | 2 | 1 | 0 |
| C17 | 0 | 0 | 0 |
| C18 | 10 | 10 | 0 |
| C19 | 3 | 3 | 1 |
| C20 | 3 | 2 | 0 |
| C21 | 0 | 0 | 0 |
| C22 | 6 | 4 | 0 |
| C23 | 3 | 3 | 1 |
| C24 | 2 | 2 | 0 |
| C25 | 9 | 7 | 0 |
| C26 | 2 | 2 | 2 |
| C27 | 2 | 2 | 0 |
| C28 | 3 | 3 | 0 |

共 23 个中心有 recalled primary，12 个中心贡献 final；本次 final 每中心最多 3 篇。首轮合格选择足够 20，真实 final 不需要第二轮补足；补足分支由 deterministic 单元测试验证。没有搜索新的参数。

## 期刊与论文领域

| 最终 venue（真实 source field） | 篇数 |
| --- | ---: |
| The Plant Journal | 4 |
| Frontiers in Plant Science | 10 |
| New Phytologist | 3 |
| Plant Physiology | 1 |
| Proceedings of the National Academy of Sciences | 1 |
| Bioinformatics | 1 |

全部 20 条 source 为 Crossref；抓取路径另保存在 private candidate evidence。领域由此次最终公开论文标题人工归组，仅用于描述分布，不写回 model、不生成 center/topic semantic name，也不参与评分：逆境与环境适应 10 篇；植物生长/发育、细胞壁与次生代谢 7 篇；作物基因组编辑 1 篇；硝酸盐吸收/同化 1 篇；转录组计算定量 1 篇。这些粗分类不是模型的互斥主题标签。

典型 final：

- 盐胁迫 multi-omics：`10.3389/fpls.2026.1970547`，C09 / 0.798387。
- 盐腺转录组与盐分泌：`10.1111/tpj.71143`，C13 / 0.732175。
- PagERF173 / nitrate uptake：`10.1093/plphys/kiag740`，C23 / 0.730381。
- MAJEC gene/isoform/transposable-element quantification：`10.1093/bioinformatics/btag737`，C11 / 0.705913。

保留原时间窗口行为：top-venue Crossref 路径沿用 `created.date-time` 写入 candidate.published，其他 public API 路径沿用其 published_at；因此此次七日验证针对现有 candidate.published 字段，不声称统一成出版社首次正式发表日期。没有在本阶段更改日期语义。

## RSS 消费与发布

现有 workflow 自动触发 [37767797276](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37767797276)，`workflow_run`，Success / 28s，source 为本次真实推荐。没有额外手动 RSS dispatch。

- 首次：changed=true，new_items=20，items=27（保留此前 7 篇）；commit `983bb8436eb220ba122a7eab0c5393be045b01ba`。
- 同 run 内再次 publication：changed=false，new_items=0，items=27；无额外提交。
- 原订阅地址普通无认证 GET 与 index 均 HTTP 200；Feed 为 RSS 2.0、27 unique GUID、27 非空摘要，20 个新 final GUID 全部在 Feed 中。
- Feed SHA256 `e5a3fe28922baa8ddef4aec56e9ea36c98358dea255e824dcbf6c8cf47a2b442`，与 Actions publication 相同；重复普通 GET 字节相同。
- 旧 7 items 的内容与首次 pubDate 完全保持；公开 item 恰好五字段白名单，没有本次 private run/model/library/encoder/center ID。
- Pages status=built；gh-pages tree 仍只有 `feed.xml`、`index.html`。源 workspace 仍 Private。

RSS publisher 同时保留识别历史 v2/v3 的能力，新增 latent-auto v1；新增针对性 non-ready skip 检查 **8 passed**（新推荐/RSS 测试组合），其余受影响测试前述 84 passed。staging 启动失败作为真实负例保留；完整实际推荐及 RSS E2E 仅上述一次，未扩大故障注入或兼容矩阵。

**P5C7 LATENT AUTO STAGING = PASS**。Production Readiness 不在本阶段验收范围，仍不能声明 PASS。
