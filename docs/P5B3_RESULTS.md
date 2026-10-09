# P5B3 — local implementation and real-library evaluation

## Frozen v1 candidate — 2026-10-01

**P5B3 QUALITY ACCEPTANCE = PASS**, bounded to the current real library as a
retrieval structure. Frozen policy: abstract-required-v2; fixed all-MiniLM-L6-v2
revision and existing window/mean/L2 encoding; auto-spherical-size-v1; experimental
0.50 cosine / 0.02 margin; >=10 distinct strong works; one-pass trimmed mean.
No further model/K/formation/membership-threshold search. Challengers are not adopted.

Private `P5B3_QUALITY_ACCEPTANCE.md` records exact revision/snapshot/input digests,
1,196 eligible / 331 ineligible, 30 raw / 29 formal, title-only invariance,
determinism, core retention and accepted broad/mixed/identity limitations.
`data/latent/frozen/p5b3-v1` is an immutable candidate capsule; the historical
HDBSCAN publisher/current pointer and monthly/incremental/lineage infrastructure
are retained. No accepted P5C staging or production rollout changed.

P5B4 now uses centroid-only `center-recall-v1`, documented privately in
`P5B4_QUALITY_ACCEPTANCE.md` and publicly in `contracts/p5b4-center-recall.md`.
The former dual-gate experiment in `P5B4_CENTER_SUPPORTED_RECALL.md` is archived;
its code is benchmark-only. Runtime loads a compact centroid projection, not
strong-member vectors. There is no ranking/semantic profile/UI/LLM hookup.
The previous benchmark section below is historical evidence, not current status.

## Current experiment definition and embedding benchmark — 2026-10-01

Title-only records are now **`ineligible_for_interest_model` / insufficient
semantic text**, never associated membership and never weak/noise. Filtering
occurs before K selection and clustering. The historical full-1,527 raw partitions
remain archived evidence; the current eligible-only cohort has **1,196** records,
with **331** ineligible records separate from all model membership statistics.

Completed the small requested encoder comparison: current MiniLM baseline,
[multilingual MiniLM-L12](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
(50-language model), and the [scientific-paper SPECTER ST conversion](https://huggingface.co/sentence-transformers/allenai-specter).
Each freshly encodes the exact same 1,527 inputs locally, then uses the unchanged
`auto_spherical-size-v1` → experimental-v1 qualification → one-pass trim.
Every model selects K=30 from N=1,196. No model-specific K/threshold adjustment.

| Model | Raw / formal centers | Strong / eligible | Eligible weak/noise |
| --- | --- | --- | --- |
| all-MiniLM-L6-v2 baseline | 30 / 29 | 1,053 / 1,196 (88.04%) | 143 (11.96%) |
| multilingual MiniLM-L12-v2 | 30 / 27 | 1,010 / 1,196 (84.45%) | 186 (15.55%) |
| allenai-specter ST | 30 / 30 | 767 / 1,196 (64.13%) | 429 (35.87%) |

Multilingual improves selected Chinese-paper placement but fragments the
single-cell core across seven formal centers. SPECTER has very high center
overlap and poor core retention under this encoding policy; its 429 weak/noise
rows all fail margin, not cosine. No challenger is accepted as clearly superior.
All models retain their released tokenizer/pooling and use one shared all-token
window/mean policy, with a uniform roundtrip-overflow split guard. This does not
test SPECTER's recommended native title[SEP]abstract/512-token CLS configuration,
so conclusions are limited to the current ZotWatch encoding constraints.

The local HDBSCAN builder also excludes title-only into `excluded_keys`, tagged
by `eligibility=abstract-required-v2` in its runtime. Legacy-policy reuse triggers
one policy rebuild and still reuses compatible vectors. Monthly scheduling,
matching, lineage and atomic persistence remain intact; no real-library source
revision was republished. The original spherical source checksum is unchanged.

Full regression: **326 passed, 16 existing environment-matrix skips**. All three
formation results reproduce exactly from their freshly generated cached vectors.
Successful benchmark encoding total: **4,581 records**; no LLM/API inference,
no model/topic names generated, no rollout or P5B4, no quality PASS. Private full
report: `docs/P5B3_EMBEDDING_MODEL_BENCHMARK.md`; private inputs, vectors, fixed
revision/snapshot checksums, partitions, representatives, review notes and
verification: `data/latent/benchmarks/2026-10-01-embedding-models-complete/`.

---

The following sections preserve the dated history and earlier experiment definitions.

2026-09-30. **Local implementation verified; real-library quality acceptance
pending.** No production deployment, staging engine rollout or sealed pin update.

The accepted P5C staging baseline remains frozen. The Web roadmap records the
new sequence: P5B3 → P5B4 center recall → P5C2 Basic/AI onboarding → staging
quality evaluation → production enablement → P5D.

## Implemented

- Separate title/abstract-only embeddings, HDBSCAN, noise exclusion, normalized
  centroids, complete-link center merging, representatives and density persistence.
- Private immutable latent revisions with atomic/checksummed pointer, closed
  schema and existing state lease; failed or drifting builds retain current state.
- Monthly cumulative material-change gate, explicit refresh, incremental embedding
  cache, one-to-one stable IDs and split/merge lineage; semantic-refresh hints.
- Explicit `zotwatch interests build/inspect` commands and a read-only desktop
  Zotero adapter into a fresh evaluation directory. No names/descriptions,
  confirmed-profile edits, LLM calls or changes to the accepted topic runner.
- Optional `latent` package extra, private-state ignore, and a dedicated offline
  HDBSCAN CI job. Existing production dependency lock remains unchanged.

Contract: [p5b3-latent-interests.md](contracts/p5b3-latent-interests.md).

## Verification

| Check | Evidence |
| --- | --- |
| Full engine regression with optional clustering runtime | 271 passed, 16 existing environment-matrix skips |
| P5B3 specific gate | 11 passed, real HDBSCAN rather than a clustering mock |
| Noise / complete-link bridge / sparse-empty libraries | PASS |
| Daily reuse / 30 days / 5% cumulative change / 30 additions / manual refresh | PASS |
| IDs / split-merge / semantic refresh / model and library isolation | PASS |
| Corruption / unknown schema / snapshot drift / atomic previous-state preservation | PASS |
| Read-only desktop extraction / trash and nonpaper exclusion | PASS |
| Wheel | PASS; all three latent modules and optional requirements present, no private SQLite/vectors |
| Git diff checks | PASS |

Tests run offline with synthetic embeddings and actual clustering/persistence.
Remote CI has not been dispatched. FAISS/SWIG and the pinned HDBSCAN/sklearn
compatibility deprecation warnings remain; they are not test failures.

## Real Zotero library experiment

Read the user's default desktop database **read-only** and extracted one local
library into an isolated temporary mirror. This is a desktop evaluation identity,
not an OAuth identity or production workspace. No source database mutation,
remote Zotero call, private data upload, or LLM invocation.

Input: 1,528 non-trash/non-attachment/non-note/non-annotation records; 1,196
with abstracts, 1 empty-text exclusion. Embedding: engine-pinned
`sentence-transformers/all-MiniLM-L6-v2` revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, title/abstract all-token windows.
All parameter comparisons below reused the same 1,527 vectors.

| min cluster | min samples | selection | centers | noise | noise % | largest cluster |
| --- | --- | --- | --- | --- | --- | --- |
| 10 | 5 | leaf (default) | 7 | 1342 | 87.9% | 84 |
| 10 | 2 | leaf | 15 | 1178 | 77.1% | 98 |
| 10 | 1 | leaf | 24 | 1113 | 72.9% | 37 |
| 5 | 2 | leaf | 34 | 1165 | 76.3% | 31 |
| 5 | 1 | leaf | 51 | 1070 | 70.1% | 21 |
| 10 | 5 | eom | 7 | 1342 | 87.9% | 84 |
| 10 | 2 | eom | 2 | 295 | 19.3% | 1215 |

Representatives in the default result form recognizable research directions,
but coverage is too narrow to accept this as the replacement recall foundation.
Simply relaxing density settings either leaves substantial noise or produces
one giant cluster. A desired count of “dozens” alone is not quality acceptance.
Default parameters are retained as an experimental baseline; no calibrated
recall threshold or automatic production selection is claimed.

Exact-input manual rebuild retained all 7 IDs and encoded 0 papers. In a separate
temporary copy, uniformly removing 77 records (~5%) and advancing 30 days from
the latest build produced 6 centers: all 6 retained their old IDs, 4 were stable
and 2 requested semantic refresh. 1,451 records remained, including 1,294 noise;
no new embeddings were required. This single deterministic perturbation is not
longitudinal semantic-stability acceptance.

Runtime: macOS arm64, Python 3.11.11, NumPy 1.26.4, hdbscan 0.8.40,
scikit-learn 1.6.1, sentence-transformers 6.1.0, transformers 5.17.0, torch 2.14.0.
This optional local runtime differs from the existing sealed Linux P2 lock; a
later workflow rollout must resolve and verify that compatibility explicitly.

Private full reports and representative titles are kept outside Git at
`/private/tmp/zotwatch-p5b3-real-library/quality-evaluation.md`. This committed
record contains aggregate counts only, with no item keys, titles or vectors.

## Remaining gate

Review representatives and noise samples; establish coverage and center stability
criteria and calibrate the clustering/embedding choices before P5B4 admission.
P5B4 also needs versioned checkpoint transport for latent state and exact latent
revision evidence. Current Actions checkpoints do not export `latent/`; monthly
reuse is verified on persistent local state only. P5C2 onboarding, semantic layer,
candidate recall, final ranking and production acceptance are not implemented
or claimed by this delivery.

## Clustering strategy benchmark follow-up (2026-09-30)

Completed the requested fixed-input comparison: HDBSCAN `10/1/leaf`, spherical
K-means K=20/25/30/35, and cosine agglomerative average-linkage at the same K.
All runs reuse the original 1,527 vectors; no embedding/LLM API calls or latent
state mutation. Nine repeated runs matched assignments and metrics exactly.
Full regression: **276 passed, 16 existing environment-matrix skips**.

Spherical K=30/35 are stronger broad-clustering candidates, with maximum clusters
of 114/94 records and common uncalibrated geometric-support coverage 85.5%/86.4%.
They keep single-cell and CRISPR cores separate, while K=20 incorrectly coassigns
17/31 CRISPR-core records with the dominant single-cell cluster. Average-linkage
produces 1,245–1,396-record giant clusters and many singletons/small groups.
These observations do not select a final K or establish semantic quality PASS.

The full requested `docs/P5B3_CLUSTERING_BENCHMARK.md` contains representative
titles/item keys and is explicitly Git-ignored, together with private metrics,
partitions and review annotations under `data/latent/benchmarks/`. It includes all
244 clusters, top-five representatives (all available members for smaller groups),
size/cohesion/nearest-center metrics, mixed-core/tail flags, and formation design
options. Existing P5B3 infrastructure and accepted P5C remain unchanged;
P5B4 and staging/production rollout have not started.

## Size-based selector follow-up (2026-09-30)

Added an independent local `auto_spherical` wrapper, without changing the
spherical fitter or publishing a latent revision. It chooses one K from
`0.8 * sqrt(N)`, coarse rounding, average-support/direction-count caps, and a
60-center budget. It performs no multi-K search. On the same frozen input it
selects K=30 and reproduces the explicit K=30 assignments, metrics and papers
exactly, in approximately 1.12 seconds including diagnostics. No new embeddings,
LLM calls or state mutation; full regression **300 passed, 16 existing skips**.

Design, size schedule, evidence and limitations are documented in
[P5B3_AUTO_SPHERICAL.md](P5B3_AUTO_SPHERICAL.md). This automates a size prior;
it does not establish optimal K, minimum per-cluster support or semantic quality
PASS. Existing HDBSCAN builder/persistence and accepted P5C remain unchanged;
P5B4 and staging/production rollout are still deferred.

## Membership qualification / one-pass trimming (2026-09-30)

Added a bounded, independent experimental stage around the unchanged raw
`auto_spherical` partition. Title-only rows do not enter qualification arithmetic
or work-identity matching. Freeze abstract-only probe centroids, apply cosine
>=0.50 and margin >=0.02 once, then normalize the strong-member mean once.
Formal centers require >=10 distinct strong works; no reassignment or iteration.
Exact DOI/title work identity is conservative; this evaluation mirror has no DOI,
so distinct support uses exact normalized titles. Thresholds remain uncalibrated.

The frozen 1,527-vector input yields **30 raw clusters, 26 formal center
candidates**, with **1,065 strong rows (69.74% of all rows; 89.05% of the 1,196
abstract-eligible rows)**. Weak/noise evidence is 462 rows (30.26%), including
331 title-only rows. C11/C21/C29/C30 do not qualify for formal centers.

Of abstract-eligible high-precision benchmark core members, single-cell retains
29/29, CRISPR 24/24, nitrate/NRT2/NRT1 19/19, salt tolerance 16/17, and annotation
10/12. No member moves between raw clusters. Known off-topic cardiomyocyte and
brain-tumor tails are excluded by the cosine probe. C16/C22 remain mixed, and
22 frozen strong rows drift below the probe threshold after trimming; these are
review flags, not a second qualification pass. On the same formal-strong
population, mean cosine rises only from 0.72030 to 0.72154. Surviving-center
overlap remains high, so this is not semantic quality acceptance.

Full regression: **316 passed, 16 existing environment-matrix skips**. Repeated
raw/formal assignments, trimmed centers and probe scores/margins match exactly.
Existing fitter source, auto-K policy, source latent pointer, vectors, monthly
rebuild, stable-ID/lineage and persistence infrastructure remain unchanged.
No embedding/LLM calls, no semantic names/descriptions, no builder publication,
P5B4 or staging/production rollout. This remains a local center-formation path.

The requested private report is `docs/P5B3_MEMBERSHIP_QUALIFICATION.md`, explicitly
Git-ignored because it contains representative titles/item keys. It compares all
30 raw clusters, before/after centroids and representatives, distinct support,
cohesion, nearest-center similarity, core preservation and residual flags.
Private numeric/member evidence and validation are in
`data/latent/benchmarks/2026-09-30-membership-qualification-final/`.
