# P2 reusable workflow contract v1

This contract defines the GitHub.com boundary between a private personal
workspace and the public `Yorks0n/ZotWatch` central engine repository.

The caller pins `.github/workflows/run.yml` to one full 40-character commit SHA.
The called job checks out the engine from `job.workflow_repository` at
`job.workflow_sha` and verifies that checkout's Git HEAD before reading private
credentials.  A private caller grants only `contents: read` and `actions: read`.
The compute workflow has no Pages or OIDC permission.

Only `schedule` and `workflow_dispatch` on a branch are trusted recommendation
events.  The workflow does not run for pull requests and does not accept a
caller-supplied engine ref, command, environment name, secret name, run ID, or
artifact ID for checkpoint restore.  Provider credentials use the fixed slots
owned by the Provider Registry.  Basic mode needs only `ZOTERO_USER_ID` and
`ZOTERO_API_KEY`.

## Private checkpoint transport

`zotwatch-state-checkpoint-v1` is a private workflow artifact.  It is never an
Actions cache entry.  Discovery verifies the current caller run, then examines
at most 15 completed runs of the same repository, caller workflow and branch,
newest first.  It accepts only trusted events and the first run containing
exactly one non-expired artifact with that fixed name whose archive and
checkpoint metadata validate.  The containing workflow's overall conclusion
is not checkpoint authority: a later Pages job may fail after compute uploaded
a valid checkpoint.  Validate-only and failed/degraded compute runs have no
valid checkpoint and are skipped.

The closed bundle contains a SQLite backup of `profile.sqlite`,
`computational/current.json`, and only the referenced immutable E4 generation.
It excludes run history, candidate cache, reports, config and credentials.
Every file has a size and SHA-256 entry.  Namespace, SQLite integrity,
library revision/identity, current pointer and E4 manifest must all agree before
installation.  E3/E4 remain the final runtime compatibility authority.  A
missing, expired, inaccessible, corrupt or stale checkpoint is a restore miss
and causes a rebuild from Zotero.

## Results and Pages

The compute workflow validates E5 `run-result-v1` and its private manifest,
then copies only declared immutable output artifacts.  Private operational
contracts, publishable recommendations and the private state checkpoint are
separate artifacts.  No directory scan or mtime chooses outputs.

Pages is a separate opt-in call to `publish-pages.yml`.  Only that job receives
`pages: write` and `id-token: write`.  It downloads the exact current-run
private/report artifact IDs passed from the compute job, verifies the sealed
engine/workspace/run identity and `outputs.publish=true`, and deploys only RSS,
HTML and recommendation JSON from the E5 allowlist.

All third-party Actions, Python runtime dependencies and the default local
embedding model revision are pinned.  Action upgrades and engine upgrades make
a new reviewed central commit; personal workspaces upgrade or roll back by
changing their single full caller SHA.
