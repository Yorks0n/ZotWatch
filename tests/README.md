# E0：1.x characterization / golden baseline

2026-09-10 首次捕获记录：在未修改的 `bf10c3c02b525ea191c5ed0f978a187a981f01c8` 上连续两次 **65 passed**。此次仅新增 tests 和独立 characterization workflow，未修改 src、原配置、生产依赖或 daily workflow。

远端封板使用 `codex/v2-e0` → `codex/v2` 的独立 PR；以该 PR 的 CI、合并提交及 `v2-e0-baseline` 内部标签记录最终结果。下文为本地捕获时的证据，不代表远端已通过；必须在远端验证和 PR 合入后才可开始 E1。

## 运行

在 ZotWatch 仓库根目录，使用 Python 3.11.11 的独立环境：

```sh
python3.11 -m venv /tmp/zotwatch-e0-venv
/tmp/zotwatch-e0-venv/bin/python -m pip install -r tests/requirements.txt
PYTHONHASHSEED=0 TZ=UTC OMP_NUM_THREADS=1 /tmp/zotwatch-e0-venv/bin/python -m pytest -c tests/pytest.ini -q
```

安装依赖需要网络；安装完成后的测试不访问网络、不下载模型、不读取个人 `.env`。测试收集前禁用 dotenv 和真实 requests/socket 连接；每个 case 使用临时目录及合成环境变量。SQLite、FAISS、candidate cache、报告都写临时目录，不 commit 数据库或索引。

测试依赖独立固定在 [requirements.txt](requirements.txt)，不使用生产 requirements 中的浮动安装结果。sentence-transformers/torch 不安装：只在模型边界注入 [固定三维单位向量](fixtures/vectors.json)。归一化、FAISS 内积检索、去重、评分、SQLite 更新及输出仍执行原代码。模型语义质量、真实 API 可用性和 harvester correctness 不属于这套离线测试。

## 覆盖与 golden

| 文件 | 覆盖 |
| --- | --- |
| [test_config_cli.py](test_config_cli.py) | 旧三 YAML、env、参数/错误、7 日过滤、预印本比例、Top N、ignore、mock push、报告文件名、原 daily workflow 静态行为 |
| [test_ingestion_dedupe.py](test_ingestion_dedupe.py) | Zotero 304/分页/新增/更新/删除/full/网络故障，真实 SQLite，DOI/URL/ID/模糊标题去重 |
| [test_candidates.py](test_candidates.py) | 冻结 public v1 分页/映射，来源路由、12 小时 cache、故障回退与 Crossref 补抓 |
| [test_profile_ranking_outputs.py](test_profile_ranking_outputs.py) | profile、真实 FAISS 保存/读取/检索、排名分项及稳定 tie、labels、recency 边界、SJR、RSS/HTML 完整 golden |
| [test_pipeline_http.py](test_pipeline_http.py) | CLI profile → watch 的真实本地链路、HTTP 重试/错误、离线防护自检 |

[goldens/metadata.json](goldens/metadata.json) 记录原提交、时钟、平台、依赖、合成输入与源码 SHA256。源码 hash 仅用于追溯捕获基线，不作为禁止后续 refactor 的测试条件。

[profile.json](goldens/profile.json)、[ranking.json](goldens/ranking.json) 由上述固定输入驱动原代码一次捕获；测试不自动重录。JSON 保留所有字段及列表顺序，浮点 abs tolerance=1e-6、relative=0；FAISS 比较邻居/距离而非跨平台二进制文件。RSS/HTML 的 ranked/empty 四份 golden 在固定 UTC 时钟下完整比较，无字段裁剪。SQLite 比较语义内容，不比较 SQLite 自己生成的 updated_at 墙钟值。

排名 golden 中 alpha 最高且 must_read，beta 为 consider，gamma/delta 同分且保持输入顺序、均 ignore。RSS 包含原 GUID/日期，缺日期条目的 pubDate 使用固定生成时间；HTML 对标题/摘要转义。向量 fixture 与作品标题严格匹配，意外输入会失败。

## 已知缺陷的观察编号

以下测试默认通过，表示捕获到原版行为，**不是应永久保留的正确性承诺**；没有用 xfail/skip 隐藏领域问题。

| 编号 | 原版行为 | 观察测试 |
| --- | --- | --- |
| BUG-C1 / C2 | public key YAML 优先于 env；MAILTO env 不被配置读取 | test_bug_config_key_priority_and_mailto |
| BUG-I1 | 删除请求使用本轮新水位，可能跳过旧水位后的删除 | test_bug_ingest_deletion_watermark_and_partial_progress |
| BUG-I2 | 分页中断后仍可能推进同步水位 | 同上 partial=True |
| BUG-I3 | full 不清理远端已不存在的旧条目 | test_bug_full_sync_retains_absent_rows |
| BUG-I4 | 内容更新保留旧 embedding | test_bug_ingest_deletion_watermark_and_partial_progress |
| BUG-W1 | watch 更新 SQLite 后不重建 profile/index | test_cli_profile_then_watch_real_pipeline；test_bug_watch_does_not_rebuild_and_keeps_ignore |
| BUG-W2 | 预印本前缀比例会丢弃前排预印本；只按 source 判断 | test_bug_preprint_prefix_cap_and_source_classification |
| BUG-F1 | public is_preprint 被丢弃 | test_public_paging_mapping_and_preprint_loss |
| BUG-F2 | 主来源失败时，成功的 top-venue 补抓也会被旧缓存覆盖 | test_bug_public_failure_cache_and_topvenue_accounting |
| BUG-F3 | 没有非空旧缓存时，来源失败可写成 fresh 空结果 | 同上 stale=False/supplement=False |
| BUG-F4 | cache 不含配置 fingerprint，改来源后仍复用 12h 内旧结果 | test_cache_boundary_and_config_not_part_of_key |

另记录：unchanged upsert 仍计入 updated；DOI URL prefix 不归一化；公共 title 不解码 HTML entity；Crossref 用 created 作为 published；权重总和 .95 未自动归一化。这些观察的修改也要在独立行为修复中解释。

## 验证证据与限制

本地环境 macOS arm64、Python 3.11.11；确切版本见 metadata。固定环境两次结果：`65 passed, 3 warnings`（0.34s / 0.33s）。3 条 warning 来自 FAISS/SWIG 类型的弃用提示；Pydantic 旧 API 的已知弃用提示由测试配置过滤，其他错误未忽略。

负向验证在临时副本中将 ranking golden 首项 score 加 1，单独运行 test_profile_and_rank_golden：`1 failed`，pytest 退出码 **1**。原 golden 未修改，证明不匹配会阻断通过。初次建测试时校正了 title entity 和 fuzzy 阈值的期望，未改生产实现。

新增 [characterization.yml](../.github/workflows/characterization.yml) 使用只读权限、不注入 Secrets，安装后连续运行两遍。Action 固定完整提交：[checkout](https://github.com/actions/checkout/commit/11bd71901bbe5b1630ceea73d27597364c9af683)、[setup-python](https://github.com/actions/setup-python/commit/a26af69be951a213d495a4c3e4e4022e16d87065)。本地捕获时尚未推送或触发 GitHub CI；Ubuntu runner 的实际证据由上述独立 PR 的 Checks 记录。

E1/E2 纯重构必须保持这些测试。E3 等有意修复缺陷时，先添加能暴露新要求的 regression，再修改实现及对应期望，列明编号、理由和受影响输出；不得批量重生 golden 消除差异。合入 E0 是进入 E1 的前置门槛，本次没有实现 AI adapter。
