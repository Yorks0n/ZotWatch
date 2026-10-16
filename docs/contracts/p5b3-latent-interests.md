# P5B3 — Latent Interest Model (local evaluation contract)

2026-10-01 frozen v1 amendment: bounded current-real-library quality acceptance
is PASS. Fixed MiniLM revision, abstract-required-v2, auto-spherical-size-v1,
experimental 0.50 cosine / 0.02 margin, >=10 distinct strong works and one-pass
trim are frozen. No more model/K/algorithm/membership-threshold search.
Broad/mixed centers are accepted retrieval structures, not guaranteed named topics.
The private immutable candidate capsule is separate from the historical HDBSCAN
current pointer; existing monthly, incremental, matching and lineage code stays.
P5B4 now uses isolated centroid-only center-recall-v1; former dual-gate work is
benchmark history. No P5B3 frozen model, rollout or accepted P5C change.

2026-10-01 eligibility amendment: current interest formation requires a nonempty
abstract. Title-only records are `ineligible_for_interest_model` with insufficient
semantic text, excluded **before** clustering/K selection. They are never
associated members and never weak/noise. Historical 1,527-record raw partitions
remain archived evidence; the current benchmark freshly encodes all 1,527 inputs
but builds only on the same 1,196 eligible records. The local HDBSCAN builder also
excludes title-only before embedding/clustering, storing them in `excluded_keys`.
Its runtime records `eligibility=abstract-required-v2`, forcing a one-time policy
rebuild while retaining compatible incremental vectors and existing matching/
revision/persistence. No source real-library revision has been republished.

The fixed spherical experimental path is `interest_formation.form_interest_model`:
eligibility filter → unchanged `auto_spherical-size-v1` → experimental cosine
0.50/margin 0.02 qualification → one strong-mean trim → >=10 distinct strong works.
No title-only labels are constructed. The optional three-model benchmark uses a
uniform roundtrip-safe variant of the existing all-token encoding policy; it does
not change the default vectorizer, clustering algorithm or production lock.

2026-09-30. Supersedes keyword suggestions as the planned interest-discovery
foundation. P5C staging acceptance remains the frozen orchestration baseline.
This phase implements an explicit local evaluation path, before P5B4 recall and
P5C2 onboarding integration. It does not change the accepted topic runner.

## Pipeline and private state

```text
committed Zotero SQLite snapshot
→ title + abstract only
→ pinned local embedding, all-token 128-token windows, mean + unit normalization
→ HDBSCAN on normalized vectors (Euclidean distance)
→ remove noise / membership strength below 0.2
→ normalized mean centroid
→ complete-link merge of highly similar original cluster centroids
→ immutable latent model revision
```

Use the optional `latent` installation extra. The clustering implementation is
`hdbscan==0.8.40` with `scikit-learn==1.6.1`; record those runtime versions and
NumPy in every model. Defaults: minimum cluster size 10, min_samples 5, leaf
selection, merge cosine 0.92, up to five representative papers per center.
Parameters are exploratory, recorded, and invalidate clustering reuse when
changed. Do not target a fixed number of clusters. Sparse/all-noise/empty-text
libraries can legitimately have zero centers and require a truthful later UX.

HDBSCAN details: [official API](https://hdbscan.readthedocs.io/en/latest/api.html).
`stability` is density persistence, weighted by retained member counts after
merging; it is not measured month-to-month consistency. `cohesion` is mean
member cosine to the merged centroid. Noise is never assigned back to a center.
Complete-link merging prevents a chain of pairwise neighbors merging distant
interests. Clustering runs directly in the embedding space; no UMAP or LLM.

Each center stores the requested `interest_id`, `centroid`, `member_count`,
`cohesion`, `stability`, `representative_items`, and `model_revision`. Private
membership keys and lineage support future stable matching. There are no names,
descriptions, editable fields or automatic writes to the confirmed Git profile.

State lives under `state/latent/generations/<revision>/model.json` and
`embeddings.npy`, with a checksum-bound atomic `current.json` pointer. The
existing cross-process state lease protects builds; readback validates the closed
schema, partition, revision, checksums and vector shape. Snapshot drift or failed
build leaves the current pointer intact. Corrupt/unknown state fails explicitly.
Old generations remain available for review; no automatic pruning in this phase.

Raw item keys, title/abstract-derived embeddings and representatives are private
computational data. Default CLI output contains revision and counts only;
`--report` explicitly writes representative titles to a private local report.
Default `data/latent` is ignored by Git and data is excluded from packages.

## Revision and stable IDs

Daily calls reuse the current revision. A rebuild requires both at least 30 days
since the last successful build and a material cumulative library change:

- additions + deletions + title/abstract edits >= 5% of the previous library; or
- at least 30 additions.

An explicit `--refresh`, library identity change, embedding compatibility change,
clustering runtime change, or parameter change rebuilds immediately. Tags,
authors and item-version-only changes do not count as semantic input changes.
No-op checks do not advance the build date or reset cumulative changes.
Compatible builds encode only new/changed text; deleted vectors are discarded.
Existing E4 vectors cannot be used because their text includes authors and tags.

Old/new matching first constructs a membership-overlap graph (intersection /
smaller center >= 0.25). One-to-one matches retain IDs when Jaccard >= 0.5 and
centroid cosine >= 0.85. Splits, merges and reorganizations receive fresh IDs
with predecessor lineage; different libraries/embedding models never share IDs.
Future semantic refresh is required for new/split/merged centers, Jaccard < 0.8,
centroid cosine < 0.95, or >= 20% edited member text. Stable centers flag reuse
of their future semantic explanation. Matching thresholds need real-library
evaluation; no LLM invocation or semantic-profile persistence exists yet.

## Evaluation entry points

```sh
python -m pip install '.[latent]'
zotwatch interests build --workspace /path/to/private-workspace --report
zotwatch interests inspect --workspace /path/to/private-workspace --report
zotwatch interests build --workspace /path/to/private-workspace --refresh --report
```

These commands require a synced `profile.sqlite` with committed library identity
and revision. They do not sync remotely or dispatch recommendations. Review
center counts, size distribution, noise, representatives, and repeatability
after controlled edits. Several hundred papers producing roughly a dozen centers
and a thousand producing dozens are hypotheses, not acceptance by construction.

A desktop-only evaluation adapter is also available:

```sh
python scripts/evaluate_latent_zotero.py --zotero-db /path/to/Zotero/zotero.sqlite \
  --evaluation-dir /path/to/new-private-evaluation
```

It opens the desktop database read-only, excludes trash/attachments/notes/
annotations, reads one local library in a snapshot transaction, and extracts
only title/abstract. The destination must be a new directory. Its synthetic
desktop evaluation identity is never an authoritative OAuth library identity.

The existing Actions checkpoint export does **not** transport `latent/` yet.
Daily reuse here is proved on persistent local state only. P5B4 must add a
versioned, reviewed checkpoint extension before scheduled ephemeral runners can
claim monthly reuse. Recall thresholds, 100–200 candidate limits, ranking,
semantic profiles and Basic/AI onboarding remain subsequent phases.

## Automatic K selection

`auto-spherical-size-v1` counts only eligible records with a nonempty abstract.
Title-only records are excluded before N and K are computed. Each rebuild selects
one K and calls the spherical fitter once; there is no multi-K quality search.

```text
N < 10: K = 0 (no fit)
otherwise:
  prior = 0.8 * sqrt(N)
  rounded = prior < 20 ? round_half_up(prior) : 5 * round_half_up(prior / 5)
  support_cap = max(1, floor(N / 20))
  K = min(max(1, rounded), support_cap, 60, distinct_directions)
```

`distinct_directions` counts distinct normalized vectors. Nonfinite or zero
vectors fail validation. The default fitter uses seed 20260930, 10 restarts and
300 maximum iterations. The support cap does not guarantee 20 members per
cluster; formal membership qualification remains a separate step. The size
prior and ceiling are engineering limits, not a cross-user optimum. Daily
recommendation reuses the published model; the lifecycle decides when to rebuild.
Implementation: [auto_spherical.py](../../zotwatch/interests/auto_spherical.py).
