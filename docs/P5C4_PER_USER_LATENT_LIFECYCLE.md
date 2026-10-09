# P5C4 — Per-user Latent Model Lifecycle

2026-10-08（Asia/Shanghai）。**B1/B2 的实现与本阶段 staging 验收通过**：独立 bootstrap、durable generations/descriptor、own-model runtime/exact reader 均完成；production rollout 仍为 NO-GO。代码仅通过新手动 staging caller 验收，未改既有用户 caller。

## 实现与合约

Engine 新增 `zotwatch/interests/lifecycle.py`、`lifecycle_transport.py` 和独立 `.github/workflows/per-user-latent-staging.yml`。原 `run.yml`、production admission、D1、现有 `watch.yml` caller 和 registry 不变。

`profile --latent-lifecycle per-user-v1` 在 Zotero preflight 明确 verify 成功并提交完整 sync 后 bootstrap；不读 confirmed profile 作为 build 前提。`watch` 保留 confirmed profile readiness 第一优先：无 profile 为 `not_ready / INTEREST_NOT_READY`，all-muted/empty topics 为 `paused`。profile bootstrap 不创建 semantic topics，不自动确认、不写 feedback。

library 编码按 accepted runtime 单次 2,000 项预算分批，独立 input snapshot 上限为 20,000 项。Builder 使用已接受 exact MiniLM revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` 和独立 12-file checksum manifest。复用 `encode_candidates → encode_existing_policy` 的 roundtrip-safe 128-token windows/equal mean/final L2，固定 fingerprint `340252ec18dd3f961b7f234c30857c51c851445705628f2e4200695020e5daa0`。排除 attachment/note/annotation/deleted，title-only 留为 ineligible 输入；调用原 `form_interest_model → auto_spherical → qualify_membership`，固定 0.50 cosine、0.02 margin、≥10 distinct strong works 和一次 trimmed centroid。复用原 `make_centers` stable-ID/split/merge matcher；不调用 HDBSCAN、不生成 LLM/semantic names、不改 K/threshold/ranking。

私有状态在 `<state>/latent-state-v1`：

- `generations/<model_revision>/generation.json`：repository/library/input snapshot、input hashes、vectors checksum、formation diagnostics、qualified members、stable IDs/lineage、built_at/dependency runtime；另有 `model.json` 与 `embeddings.npy`。
- `deployments/<deployment_revision>.json`：immutable deployment descriptor；`current.json` 原子 pointer。descriptor 绑定 numeric private repository、pseudonymous verified library identity、library revision/input digest、model revision/model-file SHA256/generation SHA256、formation policy、encoder manifest/fingerprint 和 activation lineage。
- 同一 lease 下固定 snapshot，构建后再次检查 identity/revision/input；新 generation 校验成功后，以 expected-current SHA256 CAS + atomic rename 切换。失败不覆盖旧 current。monthly gate 沿用 30 天后新增≥30 或 added/deleted/edited≥5%，未到期/无材料变化复用；显式 full 可 manual rebuild，weekly sync 不绕开 gate。
- 不同 library identity 的旧 generation 保留隔离，新库重新 bootstrap，无 vectors/stable IDs 继承；runtime 在此间拒绝旧库模型。rollback 仅允许同 repository/library、兼容 policy/encoder、有 formal centers 的已校验 immutable deployment，并生成新 activation revision。

跨 Actions run 使用独立私有 Git branch `zotwatch-latent-state-v1`，单个版本化 `latent-state-v1.tar.gz`；branch commit 包含全量 current/retained generations/deployments/manifest，不依赖 run artifacts。Git API 验证 numeric repository ID/private flag，按 exact restored commit 读取 tree/blob 并校验 Git blob identity；上传生成该 parent 的新 commit，`force:false` fast-forward 或首次 create-ref 作为 remote CAS。并发 sibling publish 拒绝；失败前 generation 不可见。持久 transport 不包含 encoder 权重，generic exact encoder 单独下载/checksum。

checkpoint-v1 closed allowlist/schema/export/import 均未扩充。新 descriptor/generation/run-deployment-evidence 有独立 v1 JSON schemas。恢复 archive 拒绝未知路径、duplicate、symlink、非 regular files、错误 checksums、cross-owner、缺 lineage 或 deployment/current 不一致；显式预算 64 MiB compressed、128 MiB payload、1024 deployments。达到预算明确失败，不删上一有效模型。

## Runtime 与 exact-run reader

Runner 在 profile readiness 之后、candidate collection 之前校验 own current descriptor/generation/model/encoder。sync 后执行 gate，再固定最终 descriptor 给本次 run；不使用个人 P5C2 model 作为默认值。missing/wrong-owner/corrupt model 明确 `LATENT_RECALL_ARTIFACT_UNAVAILABLE`，encoder 不兼容为 `LATENT_RECALL_ENCODER_UNAVAILABLE`。初次无 formal centers 可存 immutable empty generation，但 recall 为 not_ready；已有 good model 的同库 rebuild 若没有 formal centers，失败并保留旧 current。

accepted v2/v3 results、small receipt envelope 字段不变。新独立私有 `final/latent-deployment-evidence-v1.json` 封存 exact run ID、descriptor 和 canonical descriptor SHA256；materialization 验证 run/revision 一致。新 artifact 名 `zotwatch-private-per-user-latent-run-v1`，原 P5C2 artifact 名/reader 仍独立。

Web `workspacePerUserStagingConsumption` 从原 live binding/connectedLibrary 权限链获得 repository 与 verified library authority；`readPerUserExactRun` / `unpackPerUserConsumption` 是独立 reader：private repository/live authority、verified library identity、staging workflow ID/ref、exact caller head 的 immutable engine pin、attempt 1、exact run artifact SHA256、run envelope 和 descriptor checksums/revisions/encoder 全部校验。读取历史 sidecar，不读 mutable current。输出仍为原 `{outcome, consumed:{profile_blob_sha,feedback_commit_sha}}`。原 `workspaceConsumption` 和 P5C2 frozen revision gate 不变。该新 reader 尚未接入既有 first-recommendation/receipt caller；本阶段不做 caller adoption 或 production enablement。

## 测试

本地 deterministic lifecycle tests 覆盖两 repository 独立模型、输入快照/descriptor交换拒绝、库 identity 改变重新 bootstrap、monthly gates、incremental encoding、lease/CAS conflict、encoder/build/snapshot失败保留旧模型、no-formal-center rebuild拒绝、same-user rollback、跨 run archive恢复、remote fast-forward race、archive路径/link拒绝、bootstrap独立于profile、确认后own-model watch、no-profile/all-muted gates、nonpaper exclusion、原 workflow不变。

Web tests 覆盖 own arbitrary model revision、recomputed-checksum cross-owner/library/policy/encoder tampering、model/descriptor revision swap、exact workflow/run/attempt/ref/archive digest 与原 consumed projection。所有历史 P5C2 reader/fixture 和 P5C Save/Confirm/CAS/UUID/priority/mute/ranking tests 保留。

已运行：Python 全量 **416 passed / 16 existing skips**（其中 P5C4 lifecycle 28 项）；Web **610 passed**（P5C4 consumption 新增 14 项 + authenticated adapter 7 项），typecheck、9 个现有 Ajv validators、Worker validator bundle、Workerd crypto/runtime canary PASS；原 checkpoint-v1/run.yml/staging runtime/v3 schema exact bytes 与 HEAD 一致，P5C2 model/bundle digest 与 accepted baseline 一致。后续测试与真实 evidence 见下表。

## 用户 staging evidence

不提交 personal texts/member vectors/credentials；私有 evidence 在 `/private/tmp/p5c4-live` 与各私有 Git branches/Actions artifacts。

| Workspace / run | 实际结果 |
| --- | --- |
| `Yorks0n/zotwatch-p3-staging-workspace` ID 1371774683；37732755013 | main 的 legacy-v1 被新 workflow config gate 拒绝；未生成模型，保留真实负例 |
| 同 workspace，独立 `p5c4-lifecycle-staging-20261008`；37733019113 | profile-only 无 confirmed profile，真实 Zotero + encoder bootstrap `PROFILE_BUILT`，28 formal centers；持久 branch 可独立恢复 |
| 同 workspace；37733681346 | 新 runner 恢复 model；无 profile仍 `not_ready / INTEREST_NOT_READY`，无 fallback |
| 同 workspace；[37734522894](https://github.com/Yorks0n/zotwatch-p3-staging-workspace/actions/runs/37734522894) | 验收 engine `2c4478bb28e58680696cbcc58bacc020ded2de26` profile bootstrap/gate/持久化 PASS；1596 paper records、1260 eligible、28 centers、library revision 10513；13项真实 snapshot隔离fault/rebuild/rollback audit全部true |
| `ykson-dev/zotwatch-p5a3-staging-20260929` ID 1394109116；[37734838883](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37734838883) | 新增手动 P5C4 workflow，不改原 watch caller；真实 connected/confirmed user profile-only bootstrap `PROFILE_BUILT`，1596 paper records、1260 eligible、28 centers；独立 model revision/digest，13 项隔离 audit 全部 true |
| 同 workspace；[37736045399](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37736045399) | final engine `90acd6867fd481930cbb055fd7f16d38538711e9` 新 Actions run，durable branch 恢复原 bootstrap descriptor/model；`succeeded / INTEREST_READY`，7 条推荐、990 条 recall decisions；13 项隔离 lifecycle audit 全部 true |
| 第二 workspace immutable Git archive fresh local restore | commit `a33eb9b59723e0d421f72f80c69c607b5c813ea6`，无需 run artifacts；真实两仓库 model/descriptor 互换、wrong repository 和 changed library 全部拒绝 |

最终无 profile workspace model revision `4693031db5e7409ab72b7050613f2b90`；model SHA256 `9a118ca9a194053b6af308b4847b72b9afcd5b224e47016fbd147868f71ff8bc`；input SHA256 `5412c8a872c74a7f1f614c49ba59bb3182d18d6af0ed8da56574b433ac83a2f5`；durable branch commit `1c13ebfd4f0954738111000b3066fd4317e1f12a`。初期 revision `2ebdb947e2b74fb3a31f3c5518f7499c` 保留在历史 generation；最终paper-records input metadata改变触发版本化model_changed，不再把非论文项目放入input snapshot。

已确认 workspace 自己的 model revision `549d00e7ed254ed19af521ddafcbee36`；model SHA256 `be4e2e21380f094713a6008c054406ce89758c6de65568f75ec968b3608ae5dc`；generation SHA256 `60b2e88636d7040a5a132952f08a67e4794c4e0621c6092a0b3c72225455f151`；library revision 10514。两 workspace 的 input digest 都是 `5412c8…83a2f5`：因为本次验证连接同库、内容相同；各自封存 input records/revision/generation，没有复制 frozen model 或共享个人 state。归属验证由 numeric repository + library identity 确立，input digest 相同不授权跨用户使用。

最终 positive operational artifact ID `11532255514`，ZIP SHA256 `0296ba6931085ac044e470745333c6c2262d2240553f84a0d0aace443f8ef654`；exact internal run `2076dab597454ca78ce2a2b8d3c3a924`。实际 `unpackPerUserConsumption` 对真实 ZIP 通过，输出原 metadata projection；实际另一 workspace descriptor、model revision、wrong repository/changed library 均拒绝（3 项独立 private-evidence tests PASS，临时测试保留在 private evidence 目录，不混入公开 fixtures）。消费 profile blob `a37df6cc16e4f528267c2779b30cfd6c0bb06516` / feedback commit `a9da77e428f2478ceb8d69f48cf365ed2b582a31`，与原用户确认一致。未调用既有 first-recommendation API，也未制造或改写 Applied receipt。

最终 private durable archive SHA256 `1225fce716b2b060c832b46b4ac33c8e6803d81d32db81b38d68e851359af083`，fresh local restore 得到同一模型与 28 centers；此前 independent Actions run 已先从 bootstrap branch 恢复。original watch caller SHA256 `445a0ec6d78401b23f1f0c073b147953caeecb11f6525b69abafdd789e0f7e3c`、config SHA256 `a96903d739d66a7e60eefa7d9fca6da4bfa32b530c88268d3bf0d09982fbb6e8` 前后相同。新增手动 caller head `be7dc6cec6e3bdd2e1c67cd41f1ce6c565d6b312`；中间 run `37735934678` 因 GitHub 编辑器追加重复 YAML 被拒绝，修正后 exact bytes readback 再 dispatch，未执行模型切换。

真实 audit 是 Actions 中恢复后的 own user snapshot/encoder/formation 代码，month clocks 与材料变化由隔离副本模拟；未修改 live Zotero library。成功 manual rebuild 复用 compatible vectors（encoded_items=0），新 revision原子切换，旧generation retained、stable IDs retained；注入build failure current不变；同用户rollback成功。与真实30天等待区分，不声称已在墙钟时间连续运行一个月。

## 复核与操作入口

隔离 engine staging branch `p5c4-per-user-lifecycle-staging` 的执行 pin 为 `90acd6867fd481930cbb055fd7f16d38538711e9`（独立提交，未混入历史 dirty tree）。前述两个 bootstrap/audit 使用 `2c4478bb28e58680696cbcc58bacc020ded2de26`，最终 pin 只补齐 2,001–20,000 项分批编码边界；后续 own-model recommendation 使用最终 pin。Web reader 保持本地待审实现，未部署 Worker/Web。

CLI `profile --latent-lifecycle per-user-v1 --latent-encoder-cache <cache>` 用于首次 bootstrap；`watch --candidate-policy center-recall-v1 --latent-lifecycle per-user-v1 --latent-encoder-cache <cache>` 使用用户模型。`--full` 显式 rebuild；正常 run 只执行原 monthly/material-change gate。Actions 先通过 `lifecycle_transport restore` 恢复 durable branch，执行后 `publish` remote CAS；`encoder` 只下载通用 exact weights。

同用户 rollback 使用 `lifecycle_transport rollback --state <fresh-restored-state> --cache <cache> --library-identity <verified-sha256> --deployment-revision <retained-activation> --expected-current <current-file-sha256>`；repository ID/credentials 来自原 Actions env。命令先验证 local scope、policy、encoder 与 expected current，再生成新 activation、remote CAS 发布。它不编辑 historical run/receipt。

## 剩余阻塞与禁止项

- 两个真实 workspace 绑定同一个 verified Zotero library；独立 repository/model lifecycle 已验收，different-library rejection/rebootstrap 由 deterministic tests 与真实 snapshot 隔离 audit 证明。第二个不同 Zotero 账号的完整 Connect→Confirm→Applied canary 仍属于后续受控启用验收，不能将本次两个仓库声称为两个不同 Zotero 用户。
- 新独立 reader 和 lifecycle 已实现，但生产 release sealing、registry/template/caller adoption、API route admission 等 production enablement 留在 B3–B5；不修改当前 receipt caller。
- 64/128 MiB 和1024 deployment预算是明确的 bounded transport边界；长期大库/retention compact另需设计，不能把超限静默当作无模型或清理旧 good generation。
- accepted 小库/空库/no formal centers 仍不能保证推荐；HTT、mixed centers等原 bounded quality局限不变；不调threshold。
- 无 production deploy、D1 write/migration、production admission、原 caller/schedule修改。新增 staging workflow仅 workflow_dispatch，无自动schedule。
