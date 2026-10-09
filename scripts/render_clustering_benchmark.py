"""Render the private P5B3 benchmark with scoped, optional review annotations."""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
from pathlib import Path


def safe(value):
    value = html.escape(" ".join(str(value).split()))
    for char in "\\`*_[|]":
        value = value.replace(char, "\\" + char)
    return value


def render(payload, reviews, output, verification=None):
    if payload["source_embeddings_sha256"] != "83a9b59778a2ae2aff7052c68f37ce800fda79cc6d39d0bd9e79f5b0f8bb0c3d":
        raise ValueError("This authored report refers only to the frozen P5B3 corpus")
    results = payload["results"]
    lines = ["## Material Passport", "",
        "- Origin Skill: academic-research-suite / experiment-agent", "- Origin Mode: run",
        "- Origin Date: 2026-09-30", "- Verification Status: " + ("VERIFIED（固定输入计算exact-match；语义质量尚未验收）" if verification and verification["status"] == "REPRODUCIBLE" else "ANALYZED（计算已完成；语义初审非人工验收）"),
        "- Version Label: p5b3_clustering_benchmark_v1", "",
        "# P5B3 Clustering Strategy Benchmark", "",
        "**P5B3 quality = NOT ACCEPTED / 待评估。P5B4 = 未进入。staging / production rollout = 未进行。**", "",
        "本报告含真实私有库的文献标题与 item keys，仅供本地审阅；完整报告和计算文件均排除在 Git 提交之外。报告中的临时主题标题与初审标记不写入 latent/semantic profile。", "",
        "## 1. 本轮判断", "",
        "Spherical K-means 是本轮更值得继续验证的 broad-clustering 候选，K=30/35 比 K=20/25 更好地保留了若干清楚主题。依据同时来自规模分布、统一弱成员诊断、核心主题去向以及代表文献与尾部样本，不以 100% 分配率判优。", "",
        "- HDBSCAN 10/1/leaf：24 个中心，414/1527 条被分配（27.1%），中心平均 cosine 0.798；覆盖不足。还有一个按中文/文件式标题聚集的混杂中心，density core 并不自动保证语义纯净。",
        "- Spherical K-means：raw coverage 均为 100%，但仅约 83.7–86.4% 符合统一未定标几何诊断阈值；这不等于语义合格覆盖率。K=30 的最大簇 114 条，K=35 为 94 条；均没有本轮定义的 giant cluster。",
        "- K=20 把 CRISPR core 的 17/31 条放进 single-cell 主簇，K=25 的 single-cell core 只有 26/37 条留在主要簇；K=30/35 保留 single-cell core 的 35/37、34/37 条，且 CRISPR core 的 31/31 条进入独立中心。",
        "- Cosine agglomerative 的本轮 **average linkage**：最大簇 1245–1396 条，占 81.5–91.4%；另产生很多 1–9 条小簇。五个关键主题都进入同一巨簇。本结论不外推到所有 hierarchical linkage。",
        "- broad clustering 仍形成语言/文档形式中心、少量弱尾部和 5–6 条小簇；K 增大后的 cohesion 上升也不足以确认 K=35 最优。", "",
        "## 2. 实验边界与输入", "",
        f"- Source model revision: `{payload['source_model_revision']}`",
        f"- Frozen embeddings: `{payload['input_shape'][0]} × {payload['input_shape'][1]}`",
        f"- embeddings.npy SHA256: `{payload['source_embeddings_sha256']}`",
        f"- Source pointer SHA256: `{payload['source_pointer_sha256']}`",
        f"- Embedding contract fingerprint: `{payload['embedding_fingerprint']}`",
        "- 标题只从既有 SQLite mirror 只读解析；不重新读远端 Zotero、不重新 embedding、不调用 LLM API。",
        "- 9 组固定实验，没有自动 K 搜索。未修改 current pointer、任何既有 generation、stable IDs、monthly rebuild、incremental embedding、persistence 或 accepted P5C staging。",
        "- 一条空文本记录不在这 1527 条向量中。记录数不是去重后的独立文献数；相同 title+abstract 的重复记录在本轮保留，并在明细报告 distinct-input 支持数。",
        "- Runtime: " + "; ".join(f"{key}={value}" for key, value in payload["runtime"].items()), "",
        "## 3. 方法与统一指标", "",
        "| 方案 | 固定配置 |",
        "| --- | --- |",
        "| HDBSCAN | min_cluster_size=10, min_samples=1, leaf; 保留 probability≥0.2；沿用原有 centroid cosine≥0.92 的 complete-link merge |",
        "| Spherical K-means | K=20/25/30/35；cosine assignment；每次更新将成员均值 centroid 再单位化；spherical K-means++；10 次固定初始化 seed=20260930…20260939；最多300轮；按 summed assigned cosine 选同一K的最好初始化 |",
        "| Cosine agglomerative | K=20/25/30/35；distance=1−cosine；average linkage；不使用 Ward，不做额外中心合并 |", "",
        "所有评估 centroid 均是各簇成员均值再归一化。cluster IDs 依成员最小 item key 排序，只在该方案内有效，不等于持久化 interest_id。", "",
        "**coverage 的两个口径必须同时读：**", "",
        "1. 原始 coverage/outlier：算法是否给出标签；K-means/hierarchical 强制分配，因此 100% / 0% 只是结构事实。HDBSCAN 按原有 noise removal 后输出。",
        "2. 统一诊断支持 coverage：已分配、raw簇大小≥10、member-to-centroid cosine≥0.50，且 `assigned cosine − best competing centroid cosine ≥ 0.02`。剩余称为 noise/弱归属/模糊，不重新分配，不重算 centroid，也未对修剪后的支持数再次筛簇。这些阈值是明确公开的实验探针，尚未定标，不是语义合格率，也不是最终 recall/acceptance 策略。",
        "3. mean/median within-cluster cosine：主表对所有已分配记录按论文加权；另列各 cluster mean 的非加权均值和各 cluster median 的中位数。singleton 的 cosine=1，会抬高宏平均，不能据此奖励 hierarchical 的小簇。",
        "4. centroid nearest：对每个 centroid 取与其它 centroid 的最大 cosine，再报告均值/中位数/最大值；每个 cluster 另有 nearest ID/score。",
        "5. minimum cluster size 与完整 size distribution 均使用 raw partition；top5 取 centroid cosine 最大的5条。小于5条的簇列出全部真实成员，不复制凑足5条。", "",
        "自动风险标记均为几何诊断：`SMALL_SUPPORT` size<10；`GIANT_REVIEW` size≥max(15%N,3N/K)；`LOW_COHESION_REVIEW` mean<0.60；`WEAK_OR_AMBIGUOUS_REVIEW` 弱或模糊占比>35%；`CENTER_OVERLAP_REVIEW` nearest cosine≥0.85。它们不等于语义混杂判定。", "",
        "语义初审由本轮助手直接在当前会话读取标题样本完成，没有调用外部 LLM、没有独立 reviewer 或用户/专家终审。范围为 top3、最低 cosine 的2条，及关键主题去向；报告额外列出完整 top5、尾部3条和沿 key 均匀抽取的最多3条。`明显混杂核心`、`核心清楚但尾部混杂`、`相邻主题偏宽` 分开标记，避免因为一个低相关尾部就否定整个中心。标题样本不能证明全簇纯度；未读全部摘要/全文。", "",
        "## 4. 九组指标总表", "",
        "| 方案 | 簇数 | raw coverage / outlier | 诊断支持 / 弱归属 | min / median / max size | within cosine mean / median | nearest mean / median / max |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        s = r["summary"]
        lines.append(f"| {r['title']} | {s['cluster_count']} | {s['coverage']:.1%} / {s['outlier_percent']:.1%} | {s['supported_coverage']:.1%} / {s['diagnostic_outlier_or_weak_percent']:.1%} | {s['minimum_cluster_size']} / {s['size_quantiles'][2]:g} / {s['maximum_cluster_size']} | {s['within_cosine_mean']:.3f} / {s['within_cosine_median']:.3f} | {s['nearest_center_mean']:.3f} / {s['nearest_center_median']:.3f} / {s['nearest_center_max']:.3f} |")
    lines += ["", "| 方案 | size 1–2 / 3–9 / 10–49 / 50–99 / 100–199 / 200+ | ≥10簇数 | 簇均值宏平均 / 簇中位数宏中位数 | 非最近 centroid 分配 |", "| --- | --- | --- | --- | --- |"]
    for r in results:
        s = r["summary"]
        bins = " / ".join(str(n) for n in s["size_bins"].values())
        useful = s["cluster_count"] - s["size_bins"]["1-2"] - s["size_bins"]["3-9"]
        lines.append(f"| {r['title']} | {bins} | {useful} | {s['cluster_mean_cosine_macro']:.3f} / {s['cluster_median_cosine_macro']:.3f} | {s['non_nearest_assignment_count']} |")
    lines += ["", "hierarchical 的最近 centroid 不等于 tree partition 的归属；出现负 margin 是正常的算法差异，但也提示以后用 centroid 做召回时需要重新校准。以上统一诊断不替代语义审查。", "",
        "## 5. 清晰主题有没有被保留或错误合并", "",
        "固定 HDBSCAN baseline 中 C04=genome annotation（12条）、C06=plant single-cell（37条）、C08=halophyte/general salt tolerance（24条）、C14=NRT2/NRT1 nitrate transport（19条）、C16=CRISPR（31条）。它们是方便比较的现有密度核心，并非人工标注 ground truth。", "",
        "下表显示每个核心的主要目的簇与保留比例。100% 只表示没有分散；多个核心进入同一簇仍可能是严重错误合并。", "",
        "| 方案 | genome annotation | single-cell | halophyte / salt | NRT2 / nitrate | CRISPR |", "| --- | --- | --- | --- | --- | --- |"]
    for r in results:
        by_core = {t["core"]: t for t in r["core_transitions"]}
        cells = [f"{by_core[c]['dominant_center']} ({by_core[c]['dominant_share']:.0%})" for c in ["C04", "C06", "C08", "C14", "C16"]]
        lines.append("| " + r["title"] + " | " + " | ".join(cells) + " |")
    lines += ["", "- K=20 的 CRISPR→C12（17/31）和 single-cell→C12（30/37）说明主题混合，尽管 C12 的几何 cohesion 达0.705。K=25 的 CRISPR 可独立成簇，但 single-cell 与一般 Arabidopsis gene regulation 混合更明显。",
        "- K=30 的 C14/C26、K=35 的 C16/C32 分别保留 single-cell 与 CRISPR 的不同中心；NRT2 在 K=30 C15、K=35 C17 中保留全部19条。",
        "- HDBSCAN C08 本身含 general salt-response 记录，拆到 halophyte adaptation 与 general salt signaling 两个中心不必然是错误 split。不能仅凭58–67%的主簇比例判断 halophyte 被破坏；需读具体成员。",
        "- genome annotation 在 K=30 C02 仍嵌在较宽的 genomic toolkit 中；K=35 C15 的代表文献更集中于 BRAKER/EviAnn/MAKER，但尾部仍含 unrelated ML/interface papers。",
        "- average-linkage 的五个核心都进入同一巨簇；这就是“保留率100%但主题分离失败”的反例。", "",
        "## 6. 可替换 center formation 的后续设计方向（仅设计，不实施）", "",
        "本轮证据支持优先验证 **broad spherical clustering + weak-membership/outlier detection**；K=30 和 K=35 保留为两个明确候选，不现在选定最终 K。可以再以经过语义审阅的 HDBSCAN cores 作为保留/拆分约束，形成 hybrid。不能把全部 HDBSCAN cores 都当可信锚点，尤其需先剔除语言/文件形式的混杂 core。", "",
        "1. formation backend 接收相同 keys/vectors，返回成员分区、noise 与算法诊断；继续交给现有 centroid、representatives、revision、stable matching、monthly rebuild 和 persistence。benchmark 标签不写 current state。",
        "2. broad clustering 后把支持不足（最终应按 distinct works/input 去重）、低 cosine、低 margin 的记录保留为弱成员/noise。禁止直接把强制分配的 singleton/语言簇提升为长期 interest。对尾部修剪后重算 centroid，阈值须经人工审查定标。",
        "3. 使用经过审阅的 high-confidence cores 检查 CRISPR/single-cell/NRT2/annotation 是否被错误合并；若需保留核心，用约束分配或对 broad cluster 再拆分，而不是把所有剩余文献强塞回少数已有核心。",
        "4. 新兴趣仍需多文献支持；semantic mixed/language/manual clusters 暂不成为正式中心。需要跨 seed/时间扰动稳定性与噪声样本审查，再设计最终 formation adapter。HDBSCAN density persistence 与 K-means 的 objective 不应伪装成同一个 stability 值。", "",
        "现有 `ensure(..., cluster=...)` 已提供 formation 注入边界，本轮没有更改 LatentModelStore/LatentParameters 的生产语义。以后持久化算法配置需在原有 revision/compatibility 机制中明确标记，且不同 backend 不能共享错误的 stability 定义。**本报告不将该设计接入 runner 或 P5B4。**", "",
        "## 7. 各方案完整 cluster 明细", "",
        "每簇均列出 size、cosine cohesion、nearest-centroid similarity、临时初审主题、混杂/支持标记、top5 和尾部/分散样本。重复标题可能来自不同 Zotero records；不代表不同独立作品。"]
    for r in results:
        annotations = reviews.get(r["name"], {})
        counts = Counter(a["verdict"] for a in annotations.values())
        lines += ["", f"### {r['title']}", "", "Config: `" + json.dumps(r["config"], ensure_ascii=False, sort_keys=True) + "`", "",
            "完整 size distribution（升序）：`" + ", ".join(str(n) for n in r["summary"]["sizes_sorted"]) + "`。", "",
            "标题样本初审数量：" + "；".join(f"{k}={v}" for k, v in sorted(counts.items())) + "。", "",
            "| Cluster | size / distinct input | mean / median cosine | nearest / cosine | 诊断支持 | 初审标记 | 临时主题 |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for c in r["clusters"]:
            a = annotations.get(c["id"], {"theme": "未完成语义初审", "verdict": "待人工审阅", "note": ""})
            lines.append(f"| {c['id']} | {c['size']} / {c.get('distinct_input_count', c['size'])} | {c['mean_cohesion']:.3f} / {c['median_cohesion']:.3f} | {c['nearest_center']} / {c['nearest_center_similarity']:.3f} | {c['supported_count']}/{c['size']} | {a['verdict']} | {safe(a['theme'])} |")
        for c in r["clusters"]:
            a = annotations.get(c["id"], {"theme": "待审阅", "verdict": "待人工审阅", "note": ""})
            lines += ["", f"#### {r['name']} / {c['id']} — {safe(a['theme'])}", "",
                f"**{a['verdict']}**。{safe(a['note'])}", "",
                "几何标记：" + ("、".join(c["flags"]) or "无") + f"；p10 cosine={c['p10_cosine']:.3f}；weak={c['weak_count']}；ambiguous={c['ambiguous_count']}；非最近 centroid 分配={c['non_nearest_assignment_count']}。", "",
                "Top 5 representative papers（小簇仅列实际条数）：", "",
                "| rank | item key | cosine | margin | paper title |", "| --- | --- | --- | --- | --- |"]
            for rank, p in enumerate(c["representatives"], 1):
                lines.append(f"| {rank} | {safe(p['key'])} | {p['cosine']:.3f} | {p['margin']:.3f} | {safe(p['title'])} |")
            lines += ["", "尾部3条及分散抽样（是定位问题的样本，不是随机统计纯度估计）：", ""]
            for field, label in [("fringe", "尾部"), ("spread_sample", "分散")]:
                for p in c[field]:
                    lines.append(f"- {label} `{safe(p['key'])}` / cosine {p['cosine']:.3f} / margin {p['margin']:.3f}: {safe(p['title'])}")
            lines.append("")
    lines += ["## 8. 复现与限制", "",
        "计算结果与 assignments 保存在本地 ignored 的 `data/latent/benchmarks/2026-09-30/`。复现固定参数，无需模型或 LLM credentials：", "",
        "```sh", "OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\",
        "  /private/tmp/zotwatch-p5b3-venv/bin/python -m zotwatch.interests.clustering_benchmark \\",
        "  --state-dir /private/tmp/zotwatch-p5b3-real-library --output-dir /path/to/new-private-output", "```", "",
        "限制：一个真实库、一个固定 embedding 模型、固定四个 K；10次初始化只用于每个K内求解，不是跨seed稳定性验收。标题样本初审没有人类/专家标签，低 margin 不等于语义错误，几何高 cohesion 不等于语义正确。数据中存在相同语义输入的重复记录和文档/非英语标题；不能把这些重复/形式簇当作长期兴趣证据。不同阈值会改变诊断支持覆盖率。hierarchical 只测试 average linkage。没有以显著性检验或总体泛化结论判优。", ""]
    if verification:
        lines += ["### 计算复现核验", "",
            f"第二次固定配置重跑：**{verification['status']}**。{verification['groups']}组的逐记录 assignments、全部数值指标和 representative/fringe/sample输出精确一致。比较排除 wall-clock timing 和后加的人工初审/去重计数字段。源 pointer/embeddings 在两次运行前后均未改变。", "",
            f"引擎测试：**{verification['tests']['passed']} passed**；{verification['tests']['existing_environment_matrix_skips']}项原有环境矩阵测试跳过；新增benchmark专项5项通过。远端CI未触发。", "",
            f"Benchmark module SHA256：`{verification['script_sha256']}`。", "",
            "| 方案 | assignments SHA256 | canonical result SHA256（排除timing/初审派生字段） |", "| --- | --- | --- |"]
        for check in verification["checks"]:
            lines.append(f"| {check['name']} | `{check['assignment_sha256']}` | `{check['result_sha256_excluding_timing_and_review_derivatives']}` |")
        lines += ["", "复现证明计算输出稳定，不证明真实语义质量、跨月稳定性或最终策略通过。**P5B3 quality PASS未声明；P5B4/rollout未进入。**", ""]
    output.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--reviews", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--verification")
    args = parser.parse_args()
    payload = json.loads(Path(args.benchmark).read_text())
    reviews = json.loads(Path(args.reviews).read_text())
    verification = json.loads(Path(args.verification).read_text()) if args.verification else None
    render(payload, reviews, Path(args.report), verification)


if __name__ == "__main__":
    main()
