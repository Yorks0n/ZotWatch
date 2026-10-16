"""Render a private audit report; no model execution or parameter selection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def render(root):
    root = Path(root)
    payload = json.loads((root / "benchmark.json").read_text())
    source = json.loads((root / "input_snapshot.json").read_text())
    title_by_key = dict(zip(source["keys"], source["titles"]))
    notes = json.loads((root / "title-review.json").read_text()) if (root / "title-review.json").exists() else {}
    if notes and notes.get("input_sha256") != payload["input_sha256"]:
        raise ValueError("Review notes do not match the frozen input")
    lines = []
    def put(line=""):
        lines.append(line)
    def score(value):
        return "—" if value is None else f"{value:.5f}"
    def safe(value):
        return value.replace("\n", " ").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")
    put("# P5B3 Embedding Model Benchmark — eligible-only interest model")
    put()
    put("2026-10-01。本地实验，没有 rollout、P5B4 或 quality PASS。包含真实私人库标题/item keys，Git-ignored。")
    put()
    put("## 新 eligibility 定义")
    put()
    put("冻结1,527个同一 title+abstract 输入，每个模型都重新本地 embedding 全部1,527条；仅1,196条非空abstract记录进入兴趣建模。331条title-only明确标记ineligible_for_interest_model / insufficient_semantic_text，未做associated membership，没有raw/formal label，不计入weak/noise。")
    put()
    put("这些title-only vectors仅为满足同一输入benchmark而保存，不能用于latent center、K、work支持数、cohesion或representatives。当前strong coverage与weak/noise fraction的分母统一是1,196 eligible records；单独报告全部输入中的strong比例，不把ineligible伪装为noise。")
    put()
    put("形成流程固定为：eligibility filter → auto_spherical-size-v1 → experimental-v1 membership qualification → one-pass trimmed centroid。N=1,196，0.8√N≈27.67、round-to-5后K=30；三模型用同一K规则、seed=20260930、10 restarts、max_iter=300，cosine≥0.50、margin≥0.02、≥10 distinct strong works。无多K搜索、silhouette、gap statistic或模型专用阈值调整。")
    put()
    put("weak/ambiguous不参与正式centroid，不调换raw label；不满足distinct support的簇不形成正式中心。只trim一次，不执行post-trim第二轮gate。representatives仅从正式strong members取，并按同一work identity规则去重。")
    put()
    put("旧P5B3_MEMBERSHIP_QUALIFICATION.md使用全1,527条raw partition、title-only计入weak/noise，是历史v1定义，不能与本报告的eligible coverage直接比较，也不能按相同C编号沿用簇名。这里不生成semantic name/description；主题名字仅用于旧审阅core的对照。")
    put()
    put("## 三个模型与选择理由")
    put()
    put("- [all-MiniLM-L6-v2 baseline](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)：保留当前384维通用sentence/paragraph模型，作为当前应用的比较基线。")
    put("- [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)：官方标注50种语言，384维；用于检验中文/英文混杂是否由embedding跨语言能力不足造成。")
    put("- [allenai-specter ST conversion](https://huggingface.co/sentence-transformers/allenai-specter)：768维、CLS pooling，明确面向科学论文title+abstract的相似性。[原SPECTER项目](https://github.com/allenai/specter)采用citation-informed文档表示；本轮选无额外adapter的ST转换版，保持只有两个challenger。")
    put()
    put("| Model | Fixed revision | Dimension |")
    put("| --- | --- | ---: |")
    for result in payload["results"]:
        spec = result["model"]
        put(f"| {spec['identifier']} | `{spec['revision']}` | {spec['dimension']} |")
    put()
    put("## Encoding protocol 与范围限制")
    put()
    put("每个模型读取相同title+newline+abstract文本，不注入作者/tag、semantic topic或LLM解释。使用模型发行的tokenizer/pooling，最多128 content tokens的连续窗口，每个chunk先归一化，再均值并归一化；所有文本tokens参与，不把长abstract静默截断。")
    put()
    put("统一安全规则：decoded窗口若re-tokenize后超过该模型的max_seq_length，就二分该窗口直至能容纳；无丢弃record，无模型-specific语义调参。multilingual的原max length是128，因此content窗口126；其余两者128。该guard为所有模型统一适用，基线本次0次拆分。batched encoding与原逐record规则在合成测试中等价，真实MiniLM相对旧缓存最大absolute difference≈2.04e-7、最低同record cosine≈0.99999999999958。")
    put()
    put("这轮比较的是三个模型在现有ZotWatch分窗/mean编码约束下的表现。特别是SPECTER没有使用原项目推荐的title[SEP]abstract、整篇512-token CLS方式；本轮不能据此推断原生SPECTER的最优能力或所有scientific-paper模型不适合。未额外运行native-policy变体。")
    put()
    put("模型cosine的几何尺度不同。固定threshold是有意不做model-specific calibration的experimental probe；高cohesion、formal中心多或coverage高均不直接代表语义更好。不同模型centroids不在同一vector space，不能跨模型直接算centroid cosine。")
    put()
    put("| Model | Fresh records | Tokens/window | Chunk count | Roundtrip splits | Encoding seconds |")
    put("| --- | ---: | ---: | ---: | ---: | ---: |")
    for result in payload["results"]:
        enc = result["encoding"]
        put(f"| {result['model']['key']} | {result['fresh_encoded_records']} | {enc['content_window_tokens']} | {enc['chunk_count']} | {enc['roundtrip_split_count']} | {result['encoding_seconds']:.2f} |")
    put()
    put("本地首次多线程执行发生encoder进程崩溃，随后统一CPU单线程；另一次尝试在multilingual roundtrip overflow guard处停止。两个未完成目录仅为开发历史，不计入完整三模型结果。最终目录三模型全部成功，无skipped输入、无fallback embeddings。耗时包括load/tokenize/encode，不包括最初网络下载。")
    put()
    put("## 统一结果")
    put()
    put("| Model | Eligible / ineligible | Raw / formal | Strong | Strong coverage eligible | Weak/noise eligible | Raw size min/median/max | Trimmed size min/max |")
    put("| --- | --- | --- | ---: | ---: | --- | --- | --- |")
    for result in payload["results"]:
        f = result["formation"]
        summary, raw = f["summary"], result["raw_diagnostics"]["summary"]
        formal = [group for group in f["qualification"]["clusters"] if group["published"]]
        put(f"| {result['model']['key']} | {summary['eligible_count']} / {summary['ineligible_count']} | {summary['raw_cluster_count']} / {summary['formal_center_count']} | {summary['strong_count']} | {summary['strong_coverage']:.2%} | {summary['weak_noise_count']} ({summary['weak_noise_fraction']:.2%}) | {raw['minimum_cluster_size']} / {raw['size_quantiles'][2]:g} / {raw['maximum_cluster_size']} | {min(g['strong_support'] for g in formal)} / {max(g['strong_support'] for g in formal)} |")
    put()
    put("所有raw partitions都100%覆盖eligible子集；这只是spherical强制分配的行为，不能作为质量证据。")
    put()
    put("| Model | Raw mean/median cosine | Trimmed formal mean/median cosine | Nearest formal mean/max similarity | Post-trim gate drift | Weak/noise reasons（可重叠） |")
    put("| --- | --- | --- | --- | ---: | --- |")
    for result in payload["results"]:
        x = np.load(root / result["model"]["key"] / "embeddings.npy", allow_pickle=False)
        f = result["formation"]
        x = x[f["eligible_input_rows"]]
        formal = [g for g in f["qualification"]["clusters"] if g["published"]]
        values = np.concatenate([x[g["probe_strong_rows"]] @ g["trimmed_centroid"] for g in formal])
        nearest = [g["nearest_center_similarity"] for g in formal]
        raw = result["raw_diagnostics"]["summary"]
        put(f"| {result['model']['key']} | {raw['within_cosine_mean']:.5f} / {raw['within_cosine_median']:.5f} | {values.mean():.5f} / {np.median(values):.5f} | {np.mean(nearest):.5f} / {max(nearest):.5f} | {sum(g['post_trim_below_probe_threshold_count'] for g in formal)} | {json.dumps(f['qualification']['summary']['weak_noise_reason_counts'])} |")
    put()
    put("## 已知清晰主题：固定HDBSCAN core")
    put()
    put("用原high-precision HDBSCAN core的同一eligible keys追踪；这是审阅参考而非ground-truth全库标签。保留数和dominant destination分别报告，避免把fragmentation隐藏在coverage里。")
    put()
    put("| Model | Core reference | Original / eligible | Formal strong | Formal destinations |")
    put("| --- | --- | --- | ---: | --- |")
    for result in payload["results"]:
        for check in result["core_checks"]:
            put(f"| {result['model']['key']} | {check['reference']} | {check['original_count']} / {check['eligible_count']} | {check['formal_strong_count']} | {json.dumps(check['formal_destinations'])} |")
    put()
    put("## 本次样本审阅")
    put()
    for paragraph in notes.get("overview", []):
        put(paragraph)
        put()
    put("这些是当前助手对固定core/代表文献样本的审阅，非独立专家标注、非全成员/全文验收。没有调用外部LLM生成解释或center names。")
    put()
    put("## 各模型全部raw clusters与formal representatives")
    put()
    for result in payload["results"]:
        spec, f = result["model"], result["formation"]
        put(f"### {spec['key']}")
        put()
        put("每个C编号仅在本模型内有效；各模型之间C编号不表示同一topic。raw/strong rows都索引eligible_input_rows，不能直接索引1,527-input顺序。")
        put()
        put("| Raw | Formal | Raw / strong / distinct | Trimmed mean/median cohesion | Nearest formal | Similarity | Flags |")
        put("| --- | --- | --- | --- | --- | ---: | --- |")
        for g in f["qualification"]["clusters"]:
            review = notes.get(spec["key"], {}).get(g["id"], {})
            flags = g["flags"] + review.get("flags", [])
            put(f"| {g['id']} | {g['published']} | {g['raw_support']} / {g['strong_support']} / {g['distinct_strong_works']} | {score(g['trimmed_cohesion'])} / {score(g.get('trimmed_median_cohesion'))} | {g.get('nearest_center') or '—'} | {score(g.get('nearest_center_similarity'))} | {', '.join(flags) or '—'} |")
        put()
        raw_by_id = {g["id"]: g for g in result["raw_diagnostics"]["clusters"]}
        for g in f["qualification"]["clusters"]:
            put(f"#### {spec['key']} / {g['id']} — {'formal candidate' if g['published'] else 'unpublished raw evidence'}")
            put()
            put(f"raw={g['raw_support']}, probe strong={g['probe_strong_support']}, distinct strong={g['distinct_strong_works']}, formal strong={g['strong_support']}。")
            review = notes.get(spec["key"], {}).get(g["id"], {})
            if review:
                put(f"样本初审 flags: {', '.join(review.get('flags', []))}; {review.get('note', '')}")
                put()
            put("Raw top5：")
            put()
            for rep in raw_by_id[g["id"]]["representatives"]:
                put(f"- {safe(rep['title'])} — `{rep['key']}`, raw cosine={score(rep['cosine'])}")
            put()
            put("Formal strong top5：")
            put()
            for rep in g["representatives"]:
                put(f"- {safe(rep['title'])} — `{rep['key']}`, trimmed cosine={score(rep['trimmed_cosine'])}, probe margin={score(rep['probe_margin'])}")
            if not g["representatives"]:
                put("不满足≥10 distinct strong works，不生成正式representatives。")
            put()
            put("Raw fringe与分散样本（沿用统一benchmark诊断）：")
            put()
            for rep in raw_by_id[g["id"]]["fringe"] + raw_by_id[g["id"]]["spread_sample"]:
                member = f["qualification"]["members"][rep["row"]]
                put(f"- {safe(title_by_key[rep['key']])} — `{rep['key']}`, cosine={score(rep['cosine'])}; {'formal strong' if member['formal_strong'] else ','.join(member['weak_noise_reasons'])}")
            put()
    put("## 追溯与基础设施")
    put()
    put(f"全部模型统一input SHA256 `{payload['input_sha256']}`；source revision `{payload['source_model_revision']}`；source vector SHA256 `{payload['source_embeddings_sha256']}`。")
    put(f"完整成功实验fresh encoded records={payload['fresh_encoded_records']}（3×1,527）；LLM calls=0，source state mutations=0。")
    put("公开权重固定revision下载到临时cache，随后offline、CPU、本机safetensors加载，trust_remote_code=False。每个snapshot文件checksum、vector checksum、私有inputs、eligible mapping、raw/formal labels、全部成员cosine/margin/reasons都保存在实验目录。")
    put(f"运行环境：`{json.dumps(payload['runtime'])}`。")
    put()
    put("现有monthly gate、stable IDs、split/merge lineage、incremental embedding、atomic revision/persistence保留。旧local HDBSCAN builder也先排除title-only；clustering_runtime记录eligibility=abstract-required-v2，旧policy因此一次性model_changed重建，仍可复用兼容cached vectors，不会静默接受旧title-only center。source历史generation/current pointer未重建。")
    put()
    put("新的三个model results只是可审阅的formal center候选，没有接入publisher、confirmed profile、recommendation dispatch或生产依赖锁。没有改当前spherical fitter/auto-K选择算法，没有新增K搜索；没有P5B4/rollout或quality PASS。")
    put()
    return "\n".join(lines)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-dir", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(arguments)
    Path(args.report).write_text(render(args.benchmark_dir))


if __name__ == "__main__":
    main()
