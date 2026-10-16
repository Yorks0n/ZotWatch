> **2026-10-01 eligibility更新：此报告保留为历史实验。当前interest model先排除title-only；其状态为ineligible_for_interest_model，不做associated membership、不计入weak/noise。最新三模型结果见 [P5B3_EMBEDDING_MODEL_BENCHMARK.md](P5B3_EMBEDDING_MODEL_BENCHMARK.md)。**

# P5B3 — auto_spherical：轻量、按库大小选择 K

2026-09-30。本地实现与评估；未启用 staging/production，未进入 P5B4，
不声明 P5B3 quality PASS。原 spherical K-means 源文件保持完全一致。

## 选择规则

首版采用确定性的规模先验，每次模型重建只拟合一个 K。
不做多 K 搜索、silhouette、gap statistic、重采样或额外模型调用。

N 是可参与聚类的 title + abstract embedding 行数，排除附件、笔记、
annotation、已删除记录和空语义输入。不是 Zotero 的全部 item 数。

```text
N < 10：K = 0，不拟合，返回全 noise

否则：
  prior = 0.8 × sqrt(N)
  rounded = prior < 20 ? round_half_up(prior)
                       : 5 × round_half_up(prior / 5)
  support_cap = max(1, floor(N / 20))
  K = min(max(1, rounded), support_cap, 60, distinct_directions)
```

`distinct_directions` 是归一化后不同向量的数量，限制重复向量导致的
不可拟合 K。它不替代 N，也不改变原 fitter 对重复论文的权重。
非法、非有限值或零向量直接报错，不静默删除或生成新 embedding。

平方根增长让文献数量增加时，中心数增长慢于库大小，避免一直按固定
“每 50 篇一个中心”线性增加兴趣。系数 0.8 是首版可解释的工程先验：
几百篇得到十几个，上千篇得到几十个；当前库落在已审阅的 K=30/35
候选范围。**它不是跨用户验证过的最优 K 估计器**，不根据已知主题名称
或人工审阅结果偷偷调整某个库的 K。

较大 prior 以 5 为粒度取整，减轻小幅文献增减导致的 K 抖动；例如
N=1,450–1,600 都选 K=30。跨分段边界仍可能改变 K，stable ID matching
仍然需要处理 split/merge。上限 60 是第一版计算和审阅预算；命中上限
输出 `maximum_centers_reached=true`，大库适用性仍需后续评估。

| 有效文献 N | 自动 K |
| --- | ---: |
| 0–9 | 0 |
| 10 | 1 |
| 50 | 2 |
| 100 | 5 |
| 200 | 10 |
| 300 | 14 |
| 500 | 18 |
| 1,000 | 25 |
| 1,527 | 30 |
| 2,000 | 35 |
| 3,000 | 45 |
| 5,000 | 55 |
| 10,000 | 60（上限） |

## 与原算法的边界

[auto_spherical.py](../zotwatch/interests/auto_spherical.py) 提供：

```python
select_spherical_k(N, distinct_directions=unique_count)  # 规则与选择记录
labels, info = auto_spherical(existing_vectors)         # 选择 + 一次原 fitter
```

`auto_spherical` 原样调用既有 `spherical_kmeans(vectors, K)`：默认仍是
seed=20260930、10 restarts、max_iter=300。一次调用内部的 10 个初始化
与原实现相同；没有针对多个 K 乘上额外搜索开销。可显式传入原有
seed/restarts/max_iter 参数，选择记录保存实际 fitter 信息和 convergence。

选择规则本身 O(1)；精确重复方向检测需排序向量，额外内存约 O(Nd)。
主要成本仍为一次固定 K 的原 spherical 拟合，约 O(restarts × iterations
× N × K × d)。评估用 O(NKd + K²d) 的原凝聚度/中心相似度诊断，
不构造 N×N 距离矩阵。

平均支持数至少约 20 只是 K 的上限约束，**不是每个簇至少 20 篇**。
局部小簇、generic 混杂簇或语义误合并仍可能存在。评估沿用 benchmark
的诊断：小簇 <10 篇、低凝聚度、弱/模糊归属、中心重叠 ≥0.85、巨型簇。
这些告警不参与选 K，也不触发自动重试、合并、删簇或重新分配。
弱归属诊断仍用 support≥10、cosine≥0.50、margin≥0.02；尚未校准，
不转换成实际 outlier 标签。返回的原始 labels 保留原算法行为。

当前是独立可替换的 center formation 候选模块。`interests build` 仍使用
原 HDBSCAN contract；没有把新的参数伪装成 HDBSCAN 写入现有 revision。
revision、stable ID、monthly rebuild、incremental embedding、representatives
与 persistence 均未改写。未来接入 builder 时应在原有参数指纹中记录
strategy、policy_version、实际 K 和 fitter 参数，并在已有重建时机选择 K；
日常 recommendation 继续复用已发布 revision，不每天重新选 K/拟合。

## 当前真实库验证

复用原 1,527×384 向量；embeddings SHA256：
`83a9b59778a2ae2aff7052c68f37ce800fda79cc6d39d0bd9e79f5b0f8bb0c3d`。
选择记录：prior=31.2615，rounded=30，support_cap=76，distinct_directions=1,506，
最终 K=30。没有重新 embedding，没有 LLM 调用，没有 latent state 写入。

| 指标 | auto_spherical |
| --- | ---: |
| 实际 cluster count | 30 |
| 原始 coverage / outlier | 100% / 0% |
| 未校准的支持归属 / 弱或模糊归属占比 | 85.53% / 14.47% |
| cluster size min / median / max | 5 / 44.5 / 114 |
| <10 篇小簇 | 2 簇，11 篇 |
| within-cluster cosine mean / median（按论文） | 0.68138 / 0.70322 |
| nearest-other-centroid cosine mean / median / max | 0.72051 / 0.76938 / 0.86437 |
| 有几何诊断告警的簇 | 9 |
| giant review 告警 | 0 |
| 拟合调用数 / 内部 restarts | 1 / 10 |
| 本地拟合与诊断耗时 | 约 1.12 秒 |

耗时来自 macOS arm64、Python 3.11.11、NumPy 1.26.4、scikit-learn 1.6.1，
BLAS 单线程；不是其他平台或大库的时延承诺。

与原 benchmark 显式 K=30 的 labels、全部数值诊断、代表文献和审阅样本
逐项完全相同。原 spherical 源文件 SHA256 仍为
`69d19089651d20678b473347addf9b41b2720363f20c286d09e043a54c189443`；
canonical assignment SHA256 仍为
`d82a934f9121f15066d40a00e80eca6e484852f36c66246ae4ce8d3486e2391b`。
source `current.json` 的 checksum 与原 benchmark 相同，读前/读后 vectors
和 model 完全相同。因此此前 K=30 的语义审阅结论也保留，包括小簇、
弱尾部及语言/格式混杂问题；自动 K 没有解决这些质量问题。

完整回归：**300 passed，16 个原有安装环境矩阵 skips**。
新增 24 个测试实例覆盖规模边界/单调性、粗粒度稳定区间、方向数约束、
小库无需拟合、非法输入、重复方向，以及一次原 fitter 调用和显式 K 等价。
没有推送代码或运行远端 CI。

本地评估命令（输出必须为新目录）：

```sh
/private/tmp/zotwatch-p5b3-venv/bin/python scripts/evaluate_auto_spherical.py \
  --state-dir /private/tmp/zotwatch-p5b3-real-library \
  --output-dir data/latent/benchmarks/2026-09-30-auto-spherical
```

私有 `evaluation.json` 保存选择过程、全部 cluster 指标和 top-five
representatives；`partition.npz`、`input_keys.json`、`verification.json`
保存对应分配与对照证据。该目录受现有 `/data/latent/` Git ignore 保护。
原 `P5B3_CLUSTERING_BENCHMARK.md` 保留不变。本文件仅包含聚合数值。
