# P5B5 latent-center recall runtime integration

2026-10-01。**P5B5 bounded runtime integration = PASS**。冻结的
`center-recall-v1` 已接入真实 candidate runner，保持 confirmed `topic-v1`
排名。仅本地显式启用；没有 production rollout，也没有进入 staging acceptance。

## 数据路径与执行边界

```text
committed confirmed profile snapshot
  ├─ absent → not_ready
  ├─ all-muted / empty → paused
  └─ ready
       → load standalone frozen centroid artifact
       → existing Zotero sync / computational state / candidate collection
       → center-recall-v1 (all collected work identities)
       → existing library dedupe + seven-day admission
       → existing confirmed topic-v1 ranking
       → existing preprint admission + top_n
       → private v3 result with separate recall/ranking evidence
```

- 接入点：`zotwatch/interests/runner.py` 的 `CandidateFetcher.fetch_with_outcome()`
  完整成功之后，原 dedupe/recent/ranking 之前。公开 API、各 external sources 和
  transport cache 返回的候选统一经过该边界。partial/failed acquisition 沿用原
  degraded/failed 行为，不发布部分 recommendations。
- 新 adapter：`zotwatch/interests/recall_integration.py`。复用 P5B4
  `load_center_model`、`encode_candidates`、`CenterRecallIndex`，没有第二套算法。
- 默认模型路径：`<state>/latent/recall/center-recall-v1/model.json`。
  可通过 `--latent-recall-model` 指定一个独立投影文件；不接受 P5B3 capsule 作为
  runtime 输入，也不读取 latent current pointer、memberships 或 strong-member vectors。
  一个 run 加载并固定一个 revision，中途替换文件不会改变已消费的模型。
- encoder cache：显式 `--latent-encoder-cache`，否则按
  `HUGGINGFACE_HUB_CACHE` / `HF_HUB_CACHE` / `HF_HOME/hub` / 默认 HF hub 定位。
  必须已包含 exact snapshot；recall 离线加载，不下载替代权重。
- work identity 沿用 topic-v1 的 `doi`，否则 `source:identifier`。同 key 保留 collection
  的首条记录，然后逐个 qualification；没有 individual-NN 预筛选。
  超过 2,000 个 unique candidates 返回明确的 unavailable，不截取一部分后静默排名。
- 缺少 abstract 的 candidate 记录 `insufficient_semantic_text` 并拒绝召回，其他
  candidate 正常处理；不使用 title-only fallback。
- run 文件仍在 `<state>/runs/topic-<run_id>.json`，通过 schema name/version 区分。

原计算状态构建仍服务现有 collection/profile 流程；这与读取 P5B3 strong-member
vectors 是不同的数据路径。新增 latent runtime 唯一 interest-state 输入是 centroid JSON。

## 本地启用与版本化兼容模式

```sh
zotwatch watch --workspace <workspace> \
  --candidate-policy center-recall-v1 \
  --latent-recall-model <standalone-model.json> \
  --latent-encoder-cache <local-hf-hub> \
  --machine-result
```

需要现有 Basic v2 `ranking.policy: topic-v1`、private JSON 配置。
`profile` 命令或 `legacy-v1` ranking 搭配该模式会在 storage/sync 前拒绝。
latent 路径参数搭配兼容模式也会拒绝，避免用户误以为已经启用 recall。

历史行为显式命名为 **`confirmed-topic-candidates-v1`**，CLI 可显式选择；为避免
rollout，本 slice 保持它作为默认 admission。它继续产生 accepted topic-result-v2。
`center-recall-v1` 路径失败不会自动切换到兼容模式，也不会调用 legacy individual-NN。

新增结果为 **`zotwatch-latent-topic-run-result`, schema_version=3**，仍然
`ranking_policy=topic-v1`，`candidate_policy=center-recall-v1`。独立 closed schema：
`zotwatch/resources/latent-topic-run-result-v3.schema.json`；typed contract：
`zotwatch/interests/integration_results.py`。
accepted `topic-run-result-v2.schema.json`、P5C confirmed-profile schema/CAS/receipt/UI
没有修改。v3 当前只供本地 machine result / run artifact review；未接入现有 v2
workflow materialization、receipt 或 web consumption。这是后续 staging acceptance 的边界。

## Runtime artifact contract

沿用 P5B4 的 closed `CenterRecallModel` 与 `{model, sha256}` checksum envelope。
没有导出新模型、重建中心、修改 threshold 或改变 frozen candidate。

| 字段 | Runtime 约束 |
| --- | --- |
| `interest_model_revision` | 当前被部署到 standalone model.json 的 exact frozen revision；32 位 hex |
| `source_snapshot_sha256` | exact P5B3 source snapshot digest |
| `centers[].interest_id` | stable latent interest UUID，唯一；不是 confirmed semantic topic ID |
| `centers[].centroid` | 384 维 normalized formal centroid；最多 60 个 |
| `encoder.model_identifier` | `sentence-transformers/all-MiniLM-L6-v2` |
| `encoder.model_revision` | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |
| `encoder.text_policy` | `title-abstract-token-windows-128-roundtrip-safe-mean-v2` |
| `encoder.artifact_sha256` | frozen encoder artifact manifest，包含权重及原 tokenizer/config checksums |
| `embedding_text_fingerprint` | SHA256(canonical encoder identity JSON)，包含 model/revision/dimension/text policy/artifact manifest |
| `recall_policy` | `center-recall-v1` |
| `threshold` | 本 integration 固定要求 `0.55`；其他阈值拒绝使用 |

最小 runtime 不包含 representatives；它们继续留在冻结 P5B3 artifact 中。
不加载 strong-member vectors，不恢复 dual gate，不计算 support count。
没有 members payload、top supporting works 或动态成员解释。

候选只用 `title.strip() + newline + abstract.strip()`，沿用 frozen
normalized chunks → equal-window mean → final L2 encoding，以及原 roundtrip overflow
处理。任一 formal centroid cosine >= 0.55 即 qualification；最高 passing cosine
是 best match，stable ID 确定 tie；最多保留三个 secondary matches，完全不累加中心。
没有候选要编码时保留空结果/拒绝文本 evidence；不会产生或混用 vector space。

checksum/closed schema/normalized-vector validation 失败、模型缺失、空 centers 或
非 0.55 threshold：`not_ready / LATENT_RECALL_ARTIFACT_UNAVAILABLE`。
非空候选编码的 exact encoder 文件缺失、checksum/policy/fingerprint/dimension
不兼容：`not_ready / LATENT_RECALL_ENCODER_UNAVAILABLE`；该 run 不进入 ranking。
已验证 artifact 的 revision/fingerprint 仍留在 run evidence，表示尝试使用但不可用。
candidate budget 超限：`not_ready / LATENT_RECALL_CANDIDATE_BUDGET`。

## Recall / ranking evidence

Run `evidence.latent_recall` 记录 revision、fingerprint、recall policy/version、threshold；
`evidence.recall_decisions` 记录全部 unique collected candidates 的 qualification、
best stable center ID/cosine、secondary matches 及同一 revision 元数据，包括拒绝项。
进入排名前固定的 confirmed revision 是原 `evidence.profile_blob_sha`，同时保存
`feedback_commit_sha` 与 `semantic_input_sha256`。

每条 recommendation 保留全部原 topic-v1 evidence，并新增：

- `latent_recall`：其 qualification evidence，必须 recalled，candidate ID 必须等于 work key；
- `confirmed_profile_revision`：必须等于 run 的 `profile_blob_sha`。

Closed v3 validator 检查所有 decision 与 run latent metadata 一致，以及 recommendation
与 run 中对应 decision 和 confirmed revision 一致，拒绝跨 revision 拼接。

真实 smoke 中 iPscDB（`10.1093/nar/gkag909`）的两层解释：

| 层 | 证据 |
| --- | --- |
| Recall admission | latent center `8c653b23-b40f-46b8-953c-cd4e0c5a47a5`，cosine `0.7402089995489052` >= `0.55` |
| Confirmed ranking | semantic topic `11111111-1111-4111-8111-111111111111`，raw similarity `0.6510766828793245`，priority/horizon 均 `1.0`，bonus `0.0`，最终 score `0.6510766828793245` |

`center_cosine` 没有传给 `rank()` 或加入 score。
原 threshold 0.35、priority 0.8/1.0/1.2、horizon 1.0/1.1、author/venue capped bonus
以及排序/tie 逻辑完全沿用 `ranking.py`。不存在 ranking-v2。
latent center 没有映射为 hidden confirmed topic；muted description 从 topic-v1 输入中排除。
仅与 muted topic 相关的 recalled candidate 可以在 ranking 层被拒绝。

## Integration tests

`tests/test_latent_recall_integration_p5b5.py` 使用真实 runner、SQLite、computational
state、dedupe/recent/preprint admission、topic-v1 arithmetic 和结果持久化。
synthetic encoder/source 边界用于可重复的离线 regression：

- collection → center recall → ranking，拒绝项不会进入 ranker；原 score fields 逐项相同；
- recommendation/run exact latent revision、stable center/cosine/fingerprint/threshold 与
  confirmed blob/commit trace；serialized record 校验，以及跨 revision tampering 拒绝；
- no profile、all-muted、empty confirmed topics 在 latent loading / collection 之前返回；
- 混合 active/muted profile 中 recalled candidate 无法绕过 muted topic；
- missing/corrupt artifact、空 centers、改变阈值，均 not_ready 且不 fetch/fallback；
- actual corrupted encoder weights、返回错误 fingerprint/dimension，均停止 ranking；
- duplicate work keys、missing abstract、empty candidate pool 与 partial collection；
- collection 中 confirmed profile 被更新时仍消费 captured snapshot；下一 run 全 muted 为 paused；
- 显式历史兼容模式继续 v2，CLI opt-in 输出独立 closed v3 JSON schema；unsupported scope 在 storage 前拒绝。

最终全量 **381 passed、16 existing environment-matrix skips**，其中 P5B5 新增
20 个 integration tests。既有 P5B3/P5B4/P5C-related、topic-v1、E5 regressions
一并通过；现有 dependency deprecation warnings 保留。七份正式 runner smoke result
另经 JSON Schema 验证通过，`git diff --check` 通过。

## 真实公开候选 pipeline smoke

执行脚本：`scripts/smoke_latent_recall_integration.py`。正式本次 evidence：
`data/latent/recall/p5b5-integration-v2/`（Git-ignored local artifacts）。

输入：冻结 P5B4 原 58 条 Europe PMC public-paper cohort，来自
`data/latent/recall/2026-10-01/evaluation-clean/comparison.json`。
通过真实 `CandidateFetcher` transport cache parsing / success outcome 收集；
recall 重新调用 exact 本地 MiniLM encode，topic-v1 也用真实模型推理。
没有复用 cached candidate vectors 做 runtime qualification。
历史 comparison/vector 文件只在 smoke offline 对照与 hash 验证中读取；正式 adapter 不读它们。

Confirmed profile 是显式 synthetic fixture `tests/fixtures/p5b5/confirmed-profile.json`，
提交到临时本地 `zotwatch-feedback` 分支；Zotero sync 使用既有 synthetic library fixture。
未读取或更新任何生产 profile，也没有 LLM/name-description 生成。
requests/socket 网络路径被禁止；未访问 live Zotero 或 candidate 服务。

| 检查 | 结果 |
| --- | --- |
| Whole-cohort collection → recall → topic-v1 | 58 collected → 27 recalled → 27 ranked；与冻结 centroid decisions 完全一致 |
| 原 individual-NN negatives | 8/8 全部不在 recalled set；其中 heldout 3/3，不会进入 ranking |
| single-cell / CRISPR / NRT / salt / annotation | 五类在完整 runner replay 中都进入 ranking 并生成 recommendations |
| 既有 temporal admission | 保留原 publication dates；每类以历史 as-of 日期 replay，cache 排除当时尚未发表的候选；仍执行原七天窗口 |
| all-muted | paused；禁止 collection 仍通过，无 recommendations |
| no confirmed profile | not_ready，无 recommendations |
| Score/evidence | 原 topic-v1 arithmetic 校验通过；recall 附加前后 score fields 完全相同 |
| Frozen artifact integrity | P5B3 capsule 全部文件、P5B4 model、historical comparison/candidate vectors 的运行前后 SHA256 相同 |

| Full runner replay | As-of UTC | Ranking input / recommendations |
| --- | --- | --- |
| single-cell | 2026-09-19 | 4 / 4 |
| CRISPR | 2026-09-20 | 2 / 2 |
| NRT | 2026-09-13 | 3 / 3 |
| salt | 2026-04-22 | 1 / 1 |
| annotation | 2026-09-22 | 3 / 3 |

这些数字是该时间窗所有 retained candidates 的总数；每个 run 明确断言目标 category
进入 ranking 和最终 recommendation，而不是声称每个窗口只含该 category。
Whole-cohort 对照单独覆盖 recall/ranking 边界；完整 runner replay 另验证 temporal admission。

Exact 本次消费身份：

```text
interest_model_revision: 316ccd15ed658f0c3cf89142b4ba026a
embedding_text_fingerprint: 340252ec18dd3f961b7f234c30857c51c851445705628f2e4200695020e5daa0
model_file_sha256: 0b9bca4b84b25f94d1762d61f3ec4e22b9af4eff6d7c2b68b795cfe2af25321a
confirmed_profile_revision: 4e6e471ead8940a1f0ac28a08c3014d76539dbc0
feedback_commit_sha: da59a4cbb78e43f9133a8fa88776bbda15a48da6
```

Evidence 文件：`smoke.json`（身份/checksums/run index）、`cohort-pipeline.json`（全 cohort
decisions/recommendations）、五个 `run-<category>.json`（真实 runner 的 persisted v3
result）、`all-muted.json`、`no-confirmed-profile.json`、`confirmed-profile.json`。
早期 `p5b5-integration-v1/` 保留为探索记录：它向较早 replay 提供完整 cohort，含当时
尚未发表的候选；正式结论使用纠正 collection 时间边界后的 `v2/`。

## 复现与范围

```sh
/private/tmp/zotwatch-p5b3-venv/bin/python -m pytest -q

/private/tmp/zotwatch-p5b3-venv/bin/python scripts/smoke_latent_recall_integration.py \
  --model data/latent/recall/center-recall-v1/model.json \
  --cache /private/tmp/zotwatch-p5b3-hf/hub \
  --evaluation data/latent/recall/2026-10-01/evaluation-clean \
  --output /private/tmp/p5b5-runtime-replay-new
```

Smoke output 必须是新目录；重现中的 commit/run IDs 可变化，各 result 均记录其真实
消费的 exact revisions。候选文本、固定 encoder、recall policy/threshold 保持不变。

这是 bounded integration evidence：confirmed profile 与 sync 为 synthetic fixtures，
public content/encoder/collection cache/recall/ranking/runner 为真实代码路径。
不是 live-service、独立专家 relevance labeling 或 production/staging acceptance。
P5B4 的 HTT positive 漏召回、mixed centers 和小样本阈值局限全部保留；没有重新调参。
后续是否进入 staging integration acceptance 由独立决定；本 slice 未修改 CI/workflow
部署、ranking weights、P5C contracts/UI、semantic naming 或 LLM 路径。
