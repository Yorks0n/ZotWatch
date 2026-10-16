# P5C8 本地代码封存 — 2026-10-09

状态：本地改动已分组提交，已有 staging 实现及 RSS v4 得到保留。此封存不改变生产部署、远端分支、caller 或模板默认 pin，也不新增生产验收声明。

## 本地版本

- 起点：`1f1045557ae7d5dd984ca2cbdd3ce51d7fd5eb4b`，原分支 `codex/p5c2-latent-staging` 保持该提交。
- 封存分支：`codex/p5c8-local-seal-20261009`。
- 本地 annotated tag：`local-p5c8-seal-20261009`，指向包含此记录的最终文档提交。
- 实现 snapshot：`14a44dabbc1be68157915a9ddba757a901706e01`。后续封存提交只补充 README、封存记录和机器清单。
- [机器清单](LOCAL_CODE_SEAL_20261009.json)保存起点、实现 snapshot、差异文件的 Git blob SHA / SHA-256 / 大小和必要检查结果。其文件范围是起点到实现 snapshot 的已提交差异，不包含清单自身。

| 提交 | 内容 |
|---|---|
| `dfd526adf9839deddd1cd69bce7f10b1f39ff8ce` | 潜在兴趣评估、冻结/导出/校验工具、历史 dual-gate benchmark、离线测试及限定范围验收文档 |
| `c837a6f314ce63663985d83fe5ec253ddc4e1955` | 每用户模型生命周期、自动推荐、过滤诊断、摘要补全/持久缓存、发表日期、工作流和 caller 示例 |
| `14a44dabbc1be68157915a9ddba757a901706e01` | RSS 发布、累积/幂等、JATS/HTML 摘要排版、测试和公开摘要预览 |

## 与 staging 发布版本对应

已将临时 staging checkout 的完整提交链导入本仓库的本地分支 `archive/p5c8-staging-source-20261009`。它指向 `967019fe6e6c3111d075b1ce4d796d0c240681c4`；以下源码提交均在该链中，不再依赖临时目录保存 Git 对象。

| 阶段 | 文档及保存的发布回执中的执行 SHA | 本地归档源码 SHA | 对应 tree |
|---|---|---|---|
| P5C4 | `90acd6867fd481930cbb055fd7f16d38538711e9` | 同左 | `dc19097694ac2af3cf3512f48e038a1eed557195` |
| P5C7 | `6013ef7e4339db0aaf15bdfb39841e447f08d555` | `50823a8ec632b37a355ed9b91cb37c88cd0c228a` | `822bbe7f3e88d15b973c21ec04fce9069d6c089d` |
| P5C8 final | `37ee8b8f53266a9f46f7003e6d7d180499be8b3f` | `967019fe6e6c3111d075b1ce4d796d0c240681c4` | `6c99f75459d8c75c394e235c013b9d3f01e41e3b` |

P5C7/P5C8 经 Git API 发布后 commit 身份与本地构造提交不同；本次独立核验归档 tree 与已保存发布回执的 tree 完全一致。以上是保存证据的核对，本次没有查询远端最新状态。

封存源码与 P5C8 归档的运行源码差异仅为：

1. `.github/workflows/characterization.yml`：保存此前未提交的独立 offline-latent CI job。
2. `scripts/github_pages_rss.py`：保存已发布 RSS v4 的摘要排版修复。publisher SHA `da1c6bf4fd14578fbf9b0a1b50e9eff7edf12074` 的发布状态沿用 [既有验收记录](RSS_FORMATTING_PENDING.md)，本次未重新联网核验。
3. `zotwatch/config/models.py`：将既有 `latent-auto-v1 → top_n=20` JSON Schema 条件纳入模型 schema 生成元数据，并修正注释缩进。
4. `zotwatch/resources/config-v2.schema.json`：按生成器重新保存稳定 JSON；条件和限制保持原样。

另将旧 `tests/test_candidates.py` 的注册日期断言更新为 P5C8 已接受的正式发表日期，并检查注册日期 provenance。没有修改或重生 E0 fixtures/goldens。`.gitignore` 增加 metadata cache/restore marker，避免本地运行状态混入源码。

## 工具与运行代码边界

- 运行入口：`zotwatch/`、`src/` 和独立 `per-user-latent-staging.yml`；当前 accepted formation 为 auto-spherical，自动推荐 policy 为 latent-auto-v1。
- `scripts/evaluate_*`、`benchmark_*`、`render_*`、`freeze_*`、`verify_*` 等用于评估和封存，不在日常推荐链路内。`scripts/benchmarks/dual_gate_recall.py` 是历史实验。
- `scripts/github_pages_rss.py` 与 RSS caller 示例组成独立发布入口。
- 私有文库、模型、向量、metadata cache 和原始验收 archive 保留在现有私有/ignored 位置；机器清单只列源码文件。

## 本次必要检查

| 检查 | 结果 |
|---|---|
| 完整 source suite（Python 3.11，固定 hash/UTC/单线程、离线） | **439 passed / 16 skipped** |
| 16 项 skip | 原有专用 wheel/editable installation 环境测试；本次未提供这些安装环境，不声称 packaging CI 通过 |
| 全部改动 Python 文件语法解析 | PASS |
| 配置 schema 生成一致性、latent 生命周期/自动推荐/metadata/RSS 回归 | 包含在上述 439 项中，PASS |
| 四份共享 latent contract 与 Web 逐字节比较 | PASS，SHA-256 见机器清单 |
| 源码密钥标记检查 | 未发现 GitHub token、私钥或指定服务 token 标记；不构成完整安全审计 |
| 分组提交前 `git diff --cached --check` | PASS |

测试保留现有 FAISS/SWIG 和 sklearn deprecation warnings；未修改测试期望以掩盖算法失败。新 schema 生成规则解决了本次发现的两项一致性失败；日期断言修正对应既有 P5C8 行为变更。

## 验收边界

P5C8 staging PASS、RSS v4 已发布的既有记录保持。首次新 schedule event 未补证的观察仍保留；生产 rollout、多用户 fresh-user canary、D1 0009、registry/template/caller adoption、Web 推荐卡片和 P5D/P5E 尚未验收。本地 tag 是代码保全标识，不能用作 production-ready release。
