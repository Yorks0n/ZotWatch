"""Render bounded private recall evidence; no new inference or tuning."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zotwatch.interests.frozen_candidate import digest, load_frozen


def render(capsule, evaluation, cohort, mixed, report):
    snapshot, _, _ = load_frozen(capsule)
    result = json.loads((Path(evaluation) / "comparison.json").read_text())
    reviewed = json.loads(Path(cohort).read_text())
    diagnostic = json.loads(Path(mixed).read_text())
    assert result["exact_snapshot_sha256"] == digest(snapshot) == diagnostic["exact_snapshot_sha256"]
    aliases = {center["interest_id"]: center["raw_cluster_alias"] for center in snapshot["centers"]}
    rows = ["> 历史dual-gate实验；正式runtime为centroid-only center-recall-v1，见P5B4_QUALITY_ACCEPTANCE.md。corroboration只用于scripts/benchmarks历史复现。", "",
        "# P5B4 — Center-supported Candidate Recall", "",
        "2026-10-01。本地独立slice完成；不rollout，不修改accepted P5C staging baseline，不接ranking/UI/LLM/semantic naming。包含私人库支持证据，Git-ignored。",
        "", "## 结论与范围", "",
        "当前真实正/负候选样本中，centroid-only与dual-gate都减少了legacy individual-NN的误召回。**尚未证明multi-member corroboration比centroid-only更稳健**：留出集centroid-only为13/13正样本、0/14实验负样本，dual为12/13、0/14；额外门槛损失一篇正样本而没有新增FP改善。保留dual-gate作为已实现的简单实验方案，不声明P5B4 quality PASS或production readiness。",
        "", "P5B3 v1 candidate 已 bounded QUALITY ACCEPTANCE = PASS并冻结，见P5B3_QUALITY_ACCEPTANCE.md。其29个formal centers是retrieval structures，不保证人类topic纯度。mixed center未blacklist或修补。",
        "", "## 固定模型与 dual gate", "",
        f"- Interest-model revision: `{snapshot['model_revision']}`",
        f"- Exact center snapshot SHA256: `{digest(snapshot)}`",
        f"- P5B3 input digest: `{snapshot['input_digest']}`",
        "- MiniLM revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`；校验与P5B3相同的本地权重文件SHA，offline CPU single-thread，无外部inference API、无LLM。",
        "- 相同 semantic input = title.strip() + newline + abstract.strip()；相同128-token windows、normalized chunk mean、最终L2。没有title-only fallback。",
        "- 使用所有formal trimmed centroids计算cosine，只对通过center gate的中心计算corroboration。该中心内所有strong works都可支持，不只representatives；weak/ineligible不参与。",
        "- 同一work identity的重复records只计一个支持，取candidate cosine最大的variant。同一center至少M个distinct strong works通过T_member；跨center不能凑支持数，单篇高相似度不能qualify。",
        "- 最佳match按qualifying center cosine排序，tie按stable ID；recall_score取该cosine，不cross-center加权累加。最多保留3个secondary matches，另记matched_center_count与failed possible-center诊断。",
        "- 每条match保存stable interest_id、center cosine、support_count、top支持work identities/item keys/cosines、exact model revision；每个candidate保存experimental参数及policy。不是最终推荐ranking。",
        "", "```text", "center cosine >= T_center", "AND", "count(distinct strong historical works in this center with cosine >= T_member) >= M", "```", "",
        "最终样本校准参数 **T_center=0.55, T_member=0.55, M=2**。这是explicit experimental参数，没有production defaults。未直接套用P5B3 library-member gate的0.50/0.02，未改P5B3 threshold。",
        "", "## 真实候选来源、标签与泄漏控制", "",
        "[Europe PMC官方REST API](https://europepmc.org/RestfulWebService)有限抓取10类公开查询，每类前6个有abstract结果，发布日期2024-01-01至2026-09-30。公开query覆盖single-cell、CRISPR、NRT、halophyte、annotation正样本，以及clinical EGFR/melanoma、mosquito surveillance、clinical visualization/recommender、dental controls。未把私人库标题/keys/abstract上传到API。raw public responses与request URL/fetch time保存在public-source目录。",
        "", "评估前按title/abstract由当前assistant审阅并冻结初始label：不是独立专家ground truth，也不是已确认用户preference。主要方法贡献可判positive，包括人类CRISPR技术；临床/其他应用被暂作相对已审阅长期兴趣的experimental negatives。3篇同时涉及epigenetics、spatial multi-omics或neuron transcriptomics的样本标ambiguous，不计TP/FP。无LLM命名或LLM评判调用。",
        "", "固定source rank偶数=calibration、奇数=heldout，不按embedding scores选样。60输入剔除1个conference/teaching record，初始59。后续bibliographic审计发现EviAnn题名末尾句号变体实际已在库中（F574HN6W/TJ9IC3DD），从heldout剔除1个positive；保留纠正前证据，**参数完全锁定，没有根据heldout重调**。最终58 candidates = calibration 15positive+13negative+2ambiguous；heldout 13positive+14negative+1ambiguous。评估脚本新增极小HTML/terminal-period overlap检查，仅防评估泄漏，不改变P5B3 work identities/输入文本/centers。",
        "", "## Bounded calibration", "",
        "三方法独立按calibration选experimental cutoff，目标positive recall≥80%，先最少FP、再最多TP、最后按preset顺序。legacy cosine仅比较nearest individual；为隔离recall结构，三者使用相同固定窗口embedding。这不是重跑旧production完整ranking或复用其旧cutoffs。legacy历史参考包含全部1527 vectors（含331title-only）；formal recall仅使用eligible formal centers/strong works。",
        "", "Legacy thresholds与centroid thresholds各仅{0.55,0.60,0.65,0.70}。dual首轮4个明确presets：(.55,.60,3)、(.60,.65,3)、(.65,.70,3)、(.60,.65,5)，最好只有9/15positive，不达标。仅参考calibration补2个preset：(.55,.55,3)、(.55,.55,2)；最后选择(.55,.55,2)。这段recall calibration与P5B3停止membership搜索是两个不同范围，没有K/model/clustering搜索。没有再扩大recall参数搜索。",
        "", "| Method | Preset | Calibration TP / FP | Positive recall |", "| --- | --- | --- | ---: |"]
    for trial in result["calibration_trials"]:
        met=trial["calibration"]
        rows.append(f"| {trial['method']} | {trial['preset']} | {met['tp']} / {met['fp']} | {met['positive_recall']:.2%} |")
    rows += ["", "## Final comparison", "", "| Split | Method | TP / positive | FP / negative | FN | Recalled / scored |", "| --- | --- | --- | --- | ---: | --- |"]
    for split in ("calibration","heldout"):
        for method in ("legacy","centroid","dual"):
            met=result["metrics"][method][split]
            rows.append(f"| {split} | {method} | {met['tp']} / {met['tp']+met['fn']} | {met['fp']} / {met['fp']+met['tn']} | {met['fn']} | {met['recalled']} / {met['scored_count']} |")
    rows += ["", "明显相关的single-cell、CRISPR、NRT、salt与annotation样本大多能召回。校准集HTT base-editing paper是三个方法共同漏召回；留出集CHO four-dimensional annotation review被centroid-only召回，但每个possible center仅1个strong supporting work，dual正确执行M=2却损失该positive。不能将此coverage损失隐去。",
        "", "### 单篇历史收藏诱发的 false-positive examples", "",
        "| Public candidate | Split | Nearest individual cosine | Best center cosine | Legacy / centroid / dual |", "| --- | --- | ---: | ---: | --- |"]
    for row in result["records"]:
        record=row["candidate"]
        if record["expected"] is False and row["legacy"]["recalled"]:
            rows.append(f"| [{record['id']}]({record['url']}) ({record['sampling_category']}) | {record['split']} | {row['legacy']['cosine']:.5f} | {row['centroid']['cosine']:.5f} | yes / no / no |")
    rows += ["", "主要nearest历史文献为孤立UNICORN EGFR临床收藏、melanoma proteomics、mosquito commensal与visualization guide。nearest cosine可高达0.76418，而最佳formal center仅0.23458；center gate即可抑制这些例子，不能把其改善全部归功于第二个member gate。",
        "", "### Mixed-center stress 与局限", "",
        "当前独立公开negative set的centroid-only FP为0，但这不验证C27或所有mixed centers安全。另用固定C27的3个已有中文成员作**非独立诊断**：排除查询自己的distinct work后统计其他strong支持；查询仍参与frozen centroid，因此不作为holdout/new-paper结果，也不计TP/FP。",
        "", "| Existing query | Frozen C27 cosine | Distinct support excluding itself at .55 | Dual condition still met |", "| --- | ---: | ---: | --- |"]
    for query in diagnostic["queries"]:
        rows.append(f"| {query['title']} | {query['frozen_centroid_cosine']:.5f} | {query['support_count_excluding_own_work']} | yes |")
    rows += ["", "果胶query获得FRD3/根系、盐角草、pitcher-plant等混杂支持；genome-sequencing query也获FRD3和盐分论文支持。这里显示**multi-member支持仍可能是同一mixed vector结构的重复证据**，不是语义主题一致性的证明。没有自动排除这些中心，也没有声称已量化independent mixed-center FP rate。", "",
        "其他限制：sample很小且targeted，不代表每天大量candidate流的base rate；experimental negative labels仍需用户偏好核验；同source relevance rank split不是随机/时间外部验证；distinct works沿用P5B3 conservative exact身份，未解决标点/HTML变体的全库去重；M=2是样本校准值，不声称跨用户通用。", "",
        "## Exact evidence and reproduction", "",
        f"- Candidate cohort file SHA256: `{result['cohort_sha256']}`",
        f"- Candidate embeddings SHA256: `{result['candidate_vector_sha256']}`",
        "- Final independent evidence: `data/latent/recall/2026-10-01/evaluation-clean/{comparison.json,candidate_embeddings.npy}`；含每条stable-ID / supporting-work完整证据。",
        "- Frozen labels/source/split: `data/latent/recall/2026-10-01/reviewed_candidates_final.json`；raw responses在public-source；初始失败、原59样本与修正后的58样本都留存，主表仅最终58样本。",
        "- Mixed diagnostic: `data/latent/recall/2026-10-01/mixed-center-diagnostic.json`；完整top supporting titles/cosines另存。",
        "- Read-only verification confirms cached-vector recall evidence and metrics exactly reproducible; no further embedding/LLM needed.", "",
        "```sh", "/private/tmp/zotwatch-p5b3-venv/bin/python scripts/verify_center_recall.py --capsule data/latent/frozen/p5b3-v1 --cohort data/latent/recall/2026-10-01/reviewed_candidates_final.json --evaluation data/latent/recall/2026-10-01/evaluation-clean",
        "/private/tmp/zotwatch-p5b3-venv/bin/python scripts/diagnose_mixed_recall.py --capsule data/latent/frozen/p5b3-v1", "```", "",
        "可用evaluate_center_recall.py在**新output目录**重新本地encode最终cohort并运行同一bounded presets；已冻结参数和center snapshot不修改。TextVectorizer/旧ranking/confirmed semantic profile/priority/mute/dispatch都未接入此slice。", "",
        "## Implementation and checks", "",
        "历史实现原为`center_recall.py`，现移至`scripts/benchmarks/dual_gate_recall.py`；该benchmark复现same-center dual gate与旧provenance，不属于当前runtime contract。没有生产CLI或enablement。最大2000 candidates、20000历史records、60 centers，M限定2..20，拒绝非unit/非finite/错误维度与revision/support错位。",
        "", "Full offline regression: **344 passed, 16 existing environment-matrix skips**。测试覆盖单篇不能qualify、duplicate不增加support、same-center support、multi-center score不加总、threshold equality、revision/eligibility/centroid一致性及capsule corruption。未下载测试模型，未调用真实网络。实际本地recall_candidates service另用1个公开positive和1个individual-NN false-positive验证，使用相同frozen encoder/center revision，分别召回与拒绝；证据为service_smoke.json。", "",
        "## All independent candidate evidence", "",
        "标题仅用于人工审阅，不作semantic naming；下面的C aliases映射到frozen stable IDs见P5B3_QUALITY_ACCEPTANCE.md。", "",
        "| Paper / short title | Split / label | Nearest cosine | Best centroid alias / cosine | Dual best alias / count | Legacy / centroid / dual |", "| --- | --- | ---: | --- | --- | --- |"]
    for row in result["records"]:
        record=row["candidate"]
        title=" ".join(record["title"].split()[:16]).replace("|","/")
        label="ambiguous" if record["expected"] is None else ("positive" if record["expected"] else "experimental negative")
        best=row["dual"]["best_match"]
        supported=f"{aliases[best['interest_id']]} / {best['support_count']}" if best else "—"
        decisions=" / ".join("yes" if row[method]["recalled"] else "no" for method in ("legacy","centroid","dual"))
        rows.append(f"| [{record['id']}]({record['url']}) {title} | {record['split']} / {label} | {row['legacy']['cosine']:.5f} | {row['centroid']['raw_alias']} / {row['centroid']['cosine']:.5f} | {supported} | {decisions} |")
    rows += ["", "### Recalled best-match provenance", ""]
    for row in result["records"]:
        best=row["dual"]["best_match"]
        if not best: continue
        rows += [f"- {row['candidate']['id']}: stable `{best['interest_id']}`, center cosine={best['center_cosine']:.5f}, distinct support={best['support_count']}, revision=`{best['interest_model_revision']}`；top works: " + ", ".join(f"`{work['key']}` ({work['cosine']:.5f})" for work in best['top_supporting_works'])]
    Path(report).write_text("\n".join(rows)+"\n")


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("capsule","evaluation","cohort","mixed","report"):
        parser.add_argument("--"+name,required=True)
    args=parser.parse_args()
    render(args.capsule,args.evaluation,args.cohort,args.mixed,args.report)
