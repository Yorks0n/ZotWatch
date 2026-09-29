# P5B1 confirmed interests

Private opt-in policy `ranking.policy: topic-v1`, with `outputs.formats: [json]`
and `outputs.publish: false`. Existing `legacy-v1` and its sealed v1 outputs
remain separately supported. The workspace template and production pins are unchanged.

The authoritative document is `feedback/interests/v1/profile.json` on the
personal private repository's `zotwatch-feedback` branch. Schema is packaged
as `interest-profile-v1.schema.json`. Web commits the complete profile and an
immutable request receipt together. Operations add/edit/remove/mute/unmute
preserve each topic's stable UUID on edit/rename. No independent revision number.

Each run resolves the branch once, reads only regular Git tree/blob objects
at that commit, verifies the blob SHA, and pins that snapshot throughout its
execution. Actions uses its existing contents-read token directly against
GitHub, never the Worker; local runs read a local feedback branch. Local topic
runs require `ZOTWATCH_WORKSPACE_REPOSITORY_ID` matching the private profile.
The Zotero user ID must match the committed library scope. Cache/checkpoint
restore cannot replace the Git authority.

Missing confirmation is `not_ready`, empty/all-muted is `paused`; both exit
cleanly without recommendations. Incompatible/invalid/inaccessible or wrong
library state fails. Profile-only may rebuild the library without confirmation.
An already running job consumes its captured revision even if a new one is saved.
The next run consumes the latest confirmed revision.

Semantic cosine must be **>= 0.35 before weighting**. Topic weights are
low/normal/high = 0.8/1/1.2; long_term/short_term = 1/1.1. Match only active
topics and take the largest weighted similarity, ties by UUID. Description is
the semantic input; name and confirmation have no score effect. Candidate
text is title+abstract, never authors. Tokenizer windows of up to 128 tokens
are encoded separately, averaged and normalized; text-policy and model
fingerprints accompany the consumed revision. No vector cache is required in
P5B1: up to 20 topic vectors are recomputed, and library generations are reused
when compatible. Editing a priority does not force a library rebuild.

Only matched candidates receive existing exact author/venue preference
bonuses of 0.02/0.05, capped together at 0.05. There is no recency, citation,
journal quality or other metadata bonus in this policy. Existing candidate
window, deduplication and preprint admission remain in place. No decay,
negative-interest filtering, topic quota, LLM or automatic suggestions.
The threshold/weights are fixed engineering constants, not quality-calibrated probabilities.

Private `zotwatch-topic-run-result` v2 carries the confirmed blob SHA, source
commit SHA, text/model fingerprints and auditable per-candidate components.
The workflow adds the exact engine SHA and caller run/repository identity in
`topic-workflow-envelope-v2.json`, uploading only a private
`zotwatch-private-topic-run-v2` artifact. No v1 public result is fabricated and
the old publication workflow cannot consume this private v2 envelope.

New engines reject normal legacy runs when a topic profile exists or cannot
be read safely. Older sealed engines cannot enforce topic readiness but do
not erase the Git profile; downgrade is not a transparent supported migration.

Tests use synthetic vectors/library responses and real disposable Git object
stores. They validate consumption/durability, not natural-language retrieval
quality. See the Web `docs/P5B1_CONTRACT.md` for save/CAS and authorization.
