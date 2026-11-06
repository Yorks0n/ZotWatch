# P5B4 — center-recall-v1

The formal candidate runtime is centroid-only. A normalized new-paper vector is
recalled exactly when its maximum formal-centroid cosine is >= the threshold.
The current bounded candidate uses **0.55**, inherited from the previous
centroid-only calibration. No new threshold/model/K/clustering search occurs.

## Runtime artifact

`center_recall_contract.CenterRecallModel` contains only formal centroids,
stable center IDs, exact P5B3 model revision/source snapshot digest, a fixed
MiniLM encoder/text-policy identity and fingerprint, `center-recall-v1`, and the
threshold. A small checksum envelope protects the JSON artifact. Unknown fields,
member payloads, duplicate IDs, invalid dimensions/unit vectors, unpinned models
and incompatible fingerprints are rejected.

`load_center_model` reads one bounded JSON file. Recommendation runtime never
loads P5B3 embeddings.npy, inputs, memberships, qualification evidence or
representatives. Offline `export_center_recall_model.py` derives this projection
once from the frozen source; it does not modify the source capsule/current pointer.

P5B3 memberships remain intact for monthly rebuilding, qualification, trimmed
centroids, stable IDs, lineage and revision reproduction. Its precomputed
representatives stay in the original latent artifact for possible future UI/LLM
explanations; there is no per-candidate historical-member scan.

## Recall API and evidence

`center_recall.recall_candidates(model_path, cache_dir, candidates)` encodes with
the same pinned MiniLM snapshot and unchanged title + newline + abstract,
normalized-chunk/mean/final-L2 policy. Candidates do not require an abstract:
when the abstract is missing, empty or whitespace-only, the title
alone is encoded and evaluated against the same centroid threshold. Only
candidates with neither title nor abstract text are excluded as
`insufficient_semantic_text`. Title + abstract inputs keep their existing
encoding. Encoder artifact identity is checked before offline local inference.

`CenterRecallIndex(model).recall(ids, vectors, embedding_text_fingerprint=...)`
validates normalized candidate vectors and the expected fingerprint, then
computes only candidate × formal-centroid cosines. Limits: 2,000 candidates,
60 centers, 384 dimensions; interest-state work is independent of library size.

Each result records candidate ID/recalled status, best matched stable interest ID
and cosine, exact P5B3 revision, embedding/text-policy fingerprint, recall policy
and threshold. Up to three passing secondary center cosines may be retained.
Best match is highest cosine; ties use stable ID. Centers are never accumulated
into an additive score. Rejected candidates have no best match. There are no
support counts, supporting-work lists or failed-corroboration diagnostics.

## Historical benchmark and acceptance scope

The dual-gate experiment and its evidence remain archived in private
`P5B4_CENTER_SUPPORTED_RECALL.md`. The implementation now lives only in
`scripts/benchmarks/dual_gate_recall.py` and benchmark-specific tests, outside
the packaged runtime contract. Its original verification scripts still work.

Private `P5B4_QUALITY_ACCEPTANCE.md` accepts the centroid-only candidate on the
same bounded 58-paper cohort. It is not a universal relevance calibration or
production acceptance. Broad/mixed C27 centers remain known limitations; zero
false positives in this small experimental negative set is not proof of purity.

There is no rollout or accepted P5C change and no ranking/priority/mute/semantic
profile/UI/LLM integration in this slice.
