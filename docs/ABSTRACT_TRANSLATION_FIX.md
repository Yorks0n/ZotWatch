# Per-paper abstract repair and translation

The daily AI delivery step preserves source abstracts in the private diagnostic
input instead of assuming every source wrote a DOI cache entry. Missing or
rejected abstracts are repaired before quality context and translation are
prepared. Repair uses the existing exact-DOI Crossref, Europe PMC, and OpenAlex
chain and respects completed lookups and their cache expiry.

If no verified abstract is available after repair:

- Keep the original recommendation and its position.
- Skip this paper's neighbor encoding and AI quality request.
- Record a review decision with `no_verified_abstract`.
- Skip the entire translation request for this paper, including its title.
- Continue quality filtering and translation for other eligible papers.

Malformed metadata is isolated to the affected paper. A quality-context service
failure preserves the original recommendations while allowing independent
translation to continue. English output and disabled features still avoid
translation requests.

The behavior applies to existing saved AI settings and settings created through
the Web configuration writer. No new setting or credential is required. Chinese
and bilingual output use the same eligibility rule for None, LLM, and Jev quality
routes.

The private `ai/metadata-repairs.json` explains provider attempts and cache reuse.
The workflow summary includes `metadata_repair_counts` and
`translation_status_counts`, so partial skips are visible without exposing
private interest context in RSS.

Production uses immutable engine and template revisions. Deploying this change
requires publishing a new engine revision and updating the existing workspace
caller and the new-workspace template/contract together. An Actions rerun using
the old pinned revision does not apply this fix.
