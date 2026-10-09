"""Frozen-cache evaluation/report of experimental one-pass membership trimming."""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import sqlite3
import sys
import time

import numpy as np
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zotwatch.interests.auto_spherical import auto_spherical
from zotwatch.interests.clustering_benchmark import SOURCE_EMBEDDINGS_SHA256, canonical_labels
from zotwatch.interests.latent import LatentModelStore
from zotwatch.interests.membership_qualification import qualify_membership

# Existing benchmark human-readable review references, never model center names.
CORE_REFERENCES = {"C04": "genome annotation", "C06": "single-cell/spatial",
    "C08": "halophyte/general salt tolerance", "C14": "nitrate/NRT2/NRT1", "C16": "CRISPR"}


def evaluate(state_dir, baseline_dir, output_dir):
    state, baseline, output = (Path(path).resolve() for path in (state_dir, baseline_dir, output_dir))
    if any(output == source or source in output.parents or output in source.parents for source in (state, baseline)):
        raise ValueError("Output must be separate from every input")
    if output.exists():
        raise ValueError("Output must be a new private directory")
    store = LatentModelStore(state)
    pointer = (store.root / "current.json").read_bytes()
    model, vectors = store.load()
    if vectors.shape != (1527, 384) or model.embeddings_sha256 != SOURCE_EMBEDDINGS_SHA256:
        raise ValueError("Requires the frozen 1527-vector P5B3 embedding cache")
    old = json.loads((baseline / "benchmark.json").read_text())
    if json.loads((baseline / "input_keys.json").read_text()) != model.embedding_keys or old["source_embeddings_sha256"] != model.embeddings_sha256:
        raise ValueError("Baseline input alignment mismatch")
    reference = next(result for result in old["results"] if result["name"] == "spherical-kmeans-30")
    fitter_sha = sha256((Path(__file__).resolve().parents[1] / "zotwatch/interests/clustering_benchmark.py").read_bytes()).hexdigest()
    if fitter_sha != json.loads((baseline / "reproducibility.json").read_text())["script_sha256"]:
        raise ValueError("Spherical source changed from the benchmark baseline")
    reviews = json.loads((baseline / "title-review.json").read_text())["spherical-kmeans-30"]
    with np.load(baseline / "partitions.npz", allow_pickle=False) as source:
        reference_labels = source["spherical-kmeans-30"].copy()
        core_labels = source["hdbscan-10-1-leaf"].copy()
    with closing(sqlite3.connect((state / "profile.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        lookup = {row[0]: row[1:] for row in conn.execute("SELECT key,title,abstract,doi FROM items")}
    keys = model.embedding_keys
    titles, abstracts, dois = map(list, zip(*(lookup[key] for key in keys)))
    for key, title, abstract in zip(keys, titles, abstracts):
        text = "\n".join(part.strip() for part in (title, abstract or "") if part.strip())
        if sha256(text.encode()).hexdigest() != model.item_text_hashes[key]:
            raise ValueError("Library metadata no longer matches cached embedding input")
    started = time.monotonic()
    with threadpool_limits(limits=1):
        fitter_labels, selector = auto_spherical(vectors)
        if not np.array_equal(canonical_labels(fitter_labels, keys), reference_labels):
            raise ValueError("Raw spherical partition differs from accepted local benchmark evidence")
        qualified = qualify_membership(vectors, fitter_labels, keys, titles, abstracts, dois=dois)
        x = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        # Original raw centroid arithmetic is comparison evidence only. It never
        # feeds the abstract-only qualification stage or any formal center.
        for group, previous in zip(qualified["clusters"], reference["clusters"]):
            if group["id"] != previous["id"]:
                raise ValueError("Diagnostic cluster alias alignment mismatch")
            center = x[group["rows"]].mean(axis=0)
            center /= np.linalg.norm(center)
            group["comparison_raw_centroid"] = center.tolist()
            group["raw_before"] = {key: previous[key] for key in ("mean_cohesion", "median_cohesion", "nearest_center_similarity", "representatives")}
            group["raw_to_probe_cosine"] = float(np.clip(center @ group["probe_centroid"], -1, 1)) if group["probe_centroid"] is not None else None
            group["raw_to_trimmed_cosine"] = float(np.clip(center @ group["trimmed_centroid"], -1, 1)) if group["trimmed_centroid"] is not None else None
            group["prior_review"] = reviews[group["id"]]
            if reviews[group["id"]]["verdict"] == "明显混杂核心":
                group["flags"].append("PRIOR_MIXED_CORE_REVIEW")
            if group["id"] in ("C11", "C16", "C30"):
                group["flags"].append("PRIOR_LANGUAGE_OR_DOCUMENT_FORM_MIX_REVIEW")
        # Compare centroid overlap over the SAME surviving center set as well as
        # previous all-30 raw references, avoiding a misleading survivor effect.
        surviving = [group for group in qualified["clusters"] if group["published"]]
        if len(surviving) > 1:
            centers = np.array([group["comparison_raw_centroid"] for group in surviving])
            pairwise = np.clip(centers @ centers.T, -1, 1)
            np.fill_diagonal(pairwise, -np.inf)
            for i, group in enumerate(surviving):
                group["raw_nearest_same_survivors_similarity"] = float(pairwise[i].max())
        strong_scores = np.concatenate([x[group["probe_strong_rows"]] @ group["trimmed_centroid"] for group in surviving]) if surviving else np.array([])
        old_probe_scores = np.array([member["probe_cosine"] for member in qualified["members"] if member["formal_strong"]])
        comparison = {"trimmed_cosine_mean": float(strong_scores.mean()) if len(strong_scores) else None,
            "trimmed_cosine_median": float(np.median(strong_scores)) if len(strong_scores) else None,
            "same_strong_probe_cosine_mean": float(old_probe_scores.mean()) if len(old_probe_scores) else None,
            "same_strong_probe_cosine_median": float(np.median(old_probe_scores)) if len(old_probe_scores) else None,
            "post_trim_gate_drift_count": sum(group["post_trim_below_probe_threshold_count"] for group in surviving),
            "document_title_probe_count": sum(bool(member["title_probe"]["flag"]) for member in qualified["members"]),
            "dropped_raw_clusters": [group["id"] for group in qualified["clusters"] if not group["published"]]}
        for prefix, key in (("raw_same_survivors", "raw_nearest_same_survivors_similarity"), ("trimmed_formal", "nearest_center_similarity")):
            values = np.array([group[key] for group in surviving if group.get(key) is not None])
            comparison[prefix + "_nearest_similarity"] = {name: float(function(values)) if len(values) else None
                for name, function in (("mean", np.mean), ("median", np.median), ("max", np.max))}
        core_checks = []
        by_label = {group["raw_label"]: group["id"] for group in qualified["clusters"]}
        for core, topic in CORE_REFERENCES.items():
            rows = np.flatnonzero(core_labels == int(core[1:]) - 1)
            retained = [int(row) for row in rows if qualified["formal_labels"][row] >= 0]
            destinations = {}
            for row in retained:
                cid = by_label[qualified["formal_labels"][row]]
                destinations[cid] = destinations.get(cid, 0) + 1
            core_checks.append({"benchmark_reference": topic, "core": core, "core_support": len(rows),
                "abstract_eligible": sum(bool((abstracts[row] or "").strip()) for row in rows),
                "retained_strong": len(retained), "formal_destinations": destinations,
                "excluded_members": [qualified["members"][row] for row in rows if row not in retained]})
    current, check = store.load()
    if (store.root / "current.json").read_bytes() != pointer or model != current or not np.array_equal(check, vectors):
        raise ValueError("Source latent state changed during evaluation")
    if qualified["raw_labels"] != fitter_labels.tolist():
        raise ValueError("Qualification mutated raw labels")
    formal_labels = np.array(qualified["formal_labels"], dtype=np.int64)
    if not np.all((formal_labels == -1) | (formal_labels == fitter_labels)):
        raise ValueError("Qualification reassigned a raw member")
    elapsed = time.monotonic() - started
    payload = {"schema_name": "zotwatch-membership-qualification-evaluation", "schema_version": 1,
        "source_model_revision": model.model_revision, "source_embeddings_sha256": model.embeddings_sha256,
        "source_pointer_sha256": sha256(pointer).hexdigest(), "input_shape": list(vectors.shape),
        "selector": selector, "qualification": qualified, "core_checks": core_checks, "titles": titles, "comparison": comparison,
        "encoded_items": 0, "llm_calls": 0, "state_mutations": 0, "elapsed_seconds": elapsed,
        "runtime": {"python": sys.version.split()[0], "numpy": version("numpy"), "scikit-learn": version("scikit-learn"), "blas_threads": 1},
        "verification": {"raw_partition_matches_explicit_k30": True, "raw_fitter_labels_unchanged": True, "no_reassignment": True,
            "spherical_source_unchanged": True, "spherical_source_sha256": fitter_sha,
            "source_state_unchanged": True, "raw_assignment_sha256": sha256(fitter_labels.tobytes()).hexdigest(),
            "canonical_raw_assignment_sha256": sha256(reference_labels.tobytes()).hexdigest(),
            "formal_assignment_sha256": sha256(np.array(qualified["formal_labels"], dtype=np.int64).tobytes()).hexdigest()},
        "work_identity_note": "Exact normalized DOI/title union; this isolated mirror has no DOI metadata. Conservative exact-title fallback, no fuzzy matching."}
    output.mkdir(parents=True, exist_ok=False)
    (output / "evaluation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    np.savez_compressed(output / "partitions.npz", raw_fitter_labels=fitter_labels,
        canonical_raw_labels=reference_labels, formal_labels=np.array(qualified["formal_labels"], dtype=np.int64))
    (output / "input_keys.json").write_text(json.dumps(keys) + "\n")
    return payload


def render_report(payload):
    q, lines = payload["qualification"], []
    def put(line=""):
        lines.append(line)
    def number(value):
        return "—" if value is None else f"{value:.5f}"
    def paper(rep, prefix=""):
        title = rep["title"].replace("\n", " ").replace("|", "\\|")
        score = rep.get("trimmed_cosine", rep.get("cosine"))
        return f"- {prefix}{title} — `{rep['key']}`; cosine={number(score)}"
    s = q["summary"]
    put("# P5B3 Membership Qualification / Trimmed-centroid")
    put()
    put("2026-09-30。本地 experimental v1 probe，供人工审阅；不声明 quality PASS。")
    put("本报告包含私人库标题与 item keys，保存在 Git-ignored 本地文件。")
    put()
    put("## 方法与边界")
    put()
    put("保留 auto_spherical-size-v1、K≈0.8√N、原 fitter 和所有原始 labels。当前 N=1,527、K=30；只拟合一次，没有 K optimization。")
    put()
    put("qualification 中只有非空 abstract 的成员参与计算。先在各 raw cluster 内用这些成员计算 unit probe centroid；完全无 abstract 的簇没有 probe centroid，也不作为 margin 的备选中心。")
    put("assigned cosine 与次近 centroid 的 margin 均相对于冻结的 abstract-only probe centroids（包括最后未发布的小簇）计算。title-only 的两个分数为 null。")
    put()
    put("strong probe: cosine≥0.50 且 margin≥0.02。阈值明确 experimental，未跨用户定标。唯一备选中心时 margin=2；零/抵消 centroid 不通过资格筛选。")
    put("冻结 strong membership 后，只做一次 strong rows 的 normalized mean。每簇至少 10 distinct strong works 才形成正式中心；未发布小簇的 probe-strong 行也保留为 weak/noise evidence。")
    put("不重新分配，不执行第二轮 gate，不迭代聚类。post-trim 分数变化只告警，不更改 frozen membership。所有正式 representatives 取自 strong members，按 trimmed cosine 排序并按 work 去重。")
    put()
    put("distinct work 使用精确规范化 DOI 或标题的保守连通去重；本次隔离 mirror 没有 DOI，因此仅用 NFKC/casefold/空白合并后的精确标题。可能漏掉不同标题的同一 work，也可能保守合并通用同名标题；不是完整 bibliographic entity resolution。")
    put("distinct count 用于≥10发布门槛和代表文献去重；centroid 仍按所有 strong rows 求均值，未另外改变原 mean 的重复记录权重。")
    put()
    put("只有极小标题诊断：文件名后缀 .pdf/.doc/.docx/.ppt/.pptx、边缘 PDF/full-text 包装词，以及末尾 Manual/Documentation/Tutorial/User's Guide。clean suggestion 不写回、不重 embedding、不决定资格。语言与文档形式混杂沿用原人工初审告警，不引入语言/stopword系统。")
    put("计算预算与当前本地基础设施一致：最多20,000行、60个raw簇；单次资格判定+单次trim。主要额外计算 O(NKd+K²d)，不构造 N×N 距离矩阵。")
    put()
    put("raw→probe→trimmed 三者分别记录，原 raw centroid 仅作前后对照，完全不进入资格筛选算法。strong coverage 分母是全部 1,527 行；另报 abstract-eligible 分母。")
    put()
    put("## 汇总")
    put()
    put("| 指标 | 值 |")
    put("| --- | ---: |")
    for name, value in [("raw cluster count", s["raw_cluster_count"]), ("formal center count", s["formal_center_count"]),
        ("abstract eligible / title-only excluded", f"{s['abstract_eligible_count']} / {s['title_only_excluded_count']}"),
        ("probe strong / formal strong rows", f"{s['probe_strong_count']} / {s['strong_count']}"),
        ("strong coverage（全部行）", f"{s['strong_coverage']:.2%}"),
        ("strong coverage（有 abstract 行）", f"{s['eligible_strong_coverage']:.2%}"),
        ("weak/noise rows / fraction", f"{s['weak_noise_count']} / {s['weak_noise_fraction']:.2%}"),
        ("embedding / LLM / source state writes", "0 / 0 / 0"), ("fit+qualification+comparison seconds", f"{payload['elapsed_seconds']:.3f}")]:
        put(f"| {name} | {value} |")
    put()
    put("weak/noise reasons 可重叠，不能直接相加：`" + json.dumps(s["weak_noise_reason_counts"]) + "`。")
    put()
    comp = payload["comparison"]
    put(f"原全部1,527行 raw mean/median cohesion=0.68138/0.70322；正式 strong population 的 probe mean/median={number(comp['same_strong_probe_cosine_mean'])}/{number(comp['same_strong_probe_cosine_median'])}，trimmed={number(comp['trimmed_cosine_mean'])}/{number(comp['trimmed_cosine_median'])}。")
    put(f"post-trim 有 {comp['post_trim_gate_drift_count']} 个 frozen strong rows 低于原 probe 阈值（相对于最终正式centroids）；只告警，未再次筛选。明显文档标题诊断 {comp['document_title_probe_count']} 行。")
    put()
    before, after = comp["raw_same_survivors_nearest_similarity"], comp["trimmed_formal_nearest_similarity"]
    put(f"相同26个 surviving centers 的 nearest mean/median/max：raw={number(before['mean'])}/{number(before['median'])}/{number(before['max'])}；trimmed={number(after['mean'])}/{number(after['median'])}/{number(after['max'])}。均值未降低，最大重叠仍>0.85，不能声称center separation已解决。")
    put()
    put("## 本次样本审阅结论与保留问题")
    put()
    if payload["verification"]["formal_assignment_sha256"] != "088d0f4d8b35fec3213552bc61091a407bd7b72bb889770d2bc7ca475b63d63c":
        raise ValueError("Frozen-input review notes do not apply to this formal partition")
    put("30个 raw 簇中的 C11/C21/C29/C30 未成为正式中心：分别只有3/4/3/1 distinct probe-strong works。软件手册/仪器流程形式簇不再发布；不是自动重新分配到其他主题。")
    put()
    put("single-cell 的29个有abstract core成员全部保留，其中28个仍在C14、1个仍在C09；CRISPR的24个有abstract core成员全部在C26；NRT2/NRT1 core的19个全部在C15。它们没有在此次trimming中互相混合。")
    put()
    put("salt tolerance core的17个有abstract成员保留16个，分布C01/C07；1篇一般abiotic stress/nutrient use efficiency因margin被排除。C25的Na/K转运与盐耐受代表论文保持。其余single-cell/CRISPR/salt core丢失主要来自明确要求的无abstract排除，不能解释为算法识别了这些论文不相关。")
    put("annotation core只保留10/12，FINDER与SynGAP因margin不足被排除；其中1个保留成员在C14的归属已经存在于raw partition，不是trimming重新分配。这是需人工检查的false-negative/boundary风险。")
    put()
    put("C16仍有13 distinct strong works，代表论文仍混合FRD3/铁营养、中文基因组/果胶和根发生；C22仍有38 distinct strong works，代表论文仍混合原生质体与木质素。因此达到threshold和support不保证语义单一，两簇继续标记PRIOR_MIXED_CORE_REVIEW。")
    put()
    put("CRISPR代表文献从部分无abstract的植物编辑综述转向有abstract的编辑方法与genetic screens；跨体系范围仍需按用户长期兴趣人工审阅。trimming不能补回缺abstract的清晰主题证据。")
    put()
    put("## 全部 30 个 raw clusters 的对照")
    put()
    put("C编号沿用旧 benchmark；实际 fitter label 独立保存在 raw_label 字段，不重命名或改写原 evidence。")
    put()
    put("| Raw | 发布 | raw / abstract / probe strong / distinct | 正式 strong | raw→trim cosine | 同一 strong population probe / trimmed cohesion | nearest formal similarity | flags |")
    put("| --- | --- | --- | ---: | ---: | --- | ---: | --- |")
    for g in q["clusters"]:
        put(f"| {g['id']} | {'是' if g['published'] else '否'} | {g['raw_support']} / {g['abstract_support']} / {g['probe_strong_support']} / {g['distinct_strong_works']} | {g['strong_support']} | {number(g['raw_to_trimmed_cosine'])} | {number(g['strong_cohesion_before'])} / {number(g['trimmed_cohesion'])} | {number(g.get('nearest_center_similarity'))} | {', '.join(g['flags']) or '—'} |")
    put()
    put("未发布 raw cluster 的 raw→trim 仅表示候选 strong mean，不是正式中心。cohesion 不能把原全部成员与筛后成员直接当成同一总体比较。nearest formal 只比较发布中心；逐簇另列原相同 survivors 的 nearest，区分 trimming 和删簇造成的变化。")
    put()
    put("## 已知清晰主题：原 high-precision HDBSCAN core 对照")
    put()
    put("以下名字是原 benchmark 审阅参考，不是生成的 center name/description。core retention 衡量原成员是否保留，不单独证明语义纯度。")
    put()
    put("| 既有审阅参考 | HDB core | 原支持 | 有 abstract | formal strong | 去向 |")
    put("| --- | --- | ---: | ---: | ---: | --- |")
    for check in payload["core_checks"]:
        put(f"| {check['benchmark_reference']} | {check['core']} | {check['core_support']} | {check['abstract_eligible']} | {check['retained_strong']} | {json.dumps(check['formal_destinations'])} |")
    put()
    put("## 每簇代表论文与剔除证据")
    put()
    for g in q["clusters"]:
        put(f"### {g['id']} — {'formal' if g['published'] else 'unpublished raw evidence'}")
        put()
        put(f"原有审阅注记（沿用，尚未复核为 PASS）：{g['prior_review']['verdict']}；{g['prior_review']['note']}")
        put()
        put(f"raw={g['raw_support']}, abstract={g['abstract_support']}, probe strong={g['probe_strong_support']}, distinct strong={g['distinct_strong_works']}, formal strong={g['strong_support']}。")
        put(f"raw→probe={number(g['raw_to_probe_cosine'])}, probe→trim={number(g.get('probe_to_trimmed_cosine'))}, raw→trim={number(g['raw_to_trimmed_cosine'])}。")
        put(f"原全部成员 raw cohesion={number(g['raw_before']['mean_cohesion'])}; 同一 strong 成员 probe/trim={number(g['strong_cohesion_before'])}/{number(g['trimmed_cohesion'])}。")
        if g["published"]:
            put(f"trimmed median cohesion={number(g['trimmed_median_cohesion'])}。")
        put(f"nearest raw/all30={number(g['raw_before']['nearest_center_similarity'])}; raw/same survivors={number(g.get('raw_nearest_same_survivors_similarity'))}; trimmed/formal={number(g.get('nearest_center_similarity'))} ({g.get('nearest_center') or '—'})。")
        put("flags: " + (", ".join(g["flags"]) or "none"))
        put()
        put("原 raw top 5：")
        put()
        for rep in g["raw_before"]["representatives"]:
            member = q["members"][rep["row"]]
            put(paper(rep) + f"; 结果={'strong' if member['formal_strong'] else ','.join(member['weak_noise_reasons'])}")
        put()
        put("正式 strong top 5：")
        put()
        for rep in g["representatives"]:
            put(paper(rep) + f"; probe={number(rep['probe_cosine'])}; margin={number(rep['probe_margin'])}")
        if not g["representatives"]:
            put("无正式 representatives；该 raw cluster 不发布。")
        put()
        put("未进入正式中心的样本（至多5条；完整逐项 evidence 在 evaluation.json）：")
        put()
        excluded = [q["members"][row] for row in g["rows"] if not q["members"][row]["formal_strong"]]
        excluded.sort(key=lambda m: (m["probe_cosine"] is None, m["probe_cosine"] or 0, m["key"]))
        for member in excluded[:5]:
            title = payload["titles"][member["row"]].replace("\n", " ")
            put(f"- {title} — `{member['key']}`; {','.join(member['weak_noise_reasons'])}; probe={number(member['probe_cosine'])}; margin={number(member['probe_margin'])}")
        if not excluded:
            put("无。")
        put()
        for row in g["document_title_probe_rows"][:5]:
            put(f"- 标题包装诊断 `{q['members'][row]['key']}`：{payload['titles'][row]} → {q['members'][row]['title_probe']['suggested_title']}（仅 suggestion）")
        put()
    put("## 追溯、基础设施与后续人工审查")
    put()
    put(f"source revision `{payload['source_model_revision']}`；embedding SHA256 `{payload['source_embeddings_sha256']}`。")
    put(f"原 spherical 源文件 SHA256 `{payload['verification']['spherical_source_sha256']}` 与 benchmark 相同；raw partition checksum 与原显式K=30完全相同。")
    put(f"运行环境：{json.dumps(payload['runtime'])}；耗时是当前本地样本单次观察，不是跨平台承诺。")
    put("raw labels、canonical alias labels 与 formal labels 分开存储；formal label 只允许原 label 或 -1。原 vectors 继续保存在 source cache；raw/probe/trimmed centroids、成员 cosine/margin/reasons 和 representatives 保存为私有 evaluation.json evidence。")
    put("本地模块只生成 formal center 候选，不调用现有 publisher。monthly rebuild、stable IDs、split/merge lineage、incremental embeddings、revision/persistence 原实现与 source current pointer 保留不变；尚未接入 builder 或 rollout。未来使用已有 matching/persistence 时，应以正式 strong membership 做匹配，raw evidence 独立保留，并记录 experimental policy 指纹；不伪造 density stability。")
    put("人工检查应分别审阅：清晰主题是否保留；剔除是否主要落在缺 abstract/弱归属；是否仍有语言/文档形式混杂；中心重叠是否仍偏高；代表论文变化是否符合预期。统计提升不构成 quality PASS，P5B4/rollout 继续暂停。")
    put()
    return "\n".join(lines)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report", required=True, help="Private local markdown, never published")
    args = parser.parse_args(arguments)
    report = Path(args.report).resolve()
    state, baseline = Path(args.state_dir).resolve(), Path(args.baseline_dir).resolve()
    if any(report == source or source in report.parents for source in (state, baseline)) or report.exists():
        raise ValueError("Report must be new and separate from input directories")
    payload = evaluate(args.state_dir, args.baseline_dir, args.output_dir)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(payload))
    print(json.dumps({"summary": payload["qualification"]["summary"], "core_checks": [
        {key: value for key, value in check.items() if key != "excluded_members"} for check in payload["core_checks"]],
        "elapsed_seconds": payload["elapsed_seconds"], "verification": payload["verification"]}, allow_nan=False))


if __name__ == "__main__":
    main()
