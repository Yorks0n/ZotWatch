# ZotWatch

**Turn your Zotero library into a starting point for paper discovery.**

ZotWatch builds personal interest centers from your saved papers, retrieves new literature, and delivers recommendations through a reading page and RSS. Computation runs in your own private GitHub workspace; a separate public repository hosts publishable results.

[Get started](https://zotwatch.yorks0n.com/) · [中文](README.md) · [Development notes](docs/V2_DEVELOPMENT.md)

## What is new

- **Automatic interest modeling:** sync Zotero, build a model for each user, retain versioned state, and rebuild when update conditions are met.
- **Daily recommendations:** retrieve papers related to interest centers and apply date and deduplication rules to select up to 20 papers.
- **Optional AI:** enable title/abstract translation and LLM or Jev judgment independently. Basic recommendations need no AI key; AI is off by default for new users.
- **Specific judgment context:** with private-context consent, create versioned interest portraits and retrieve similar historical papers for each candidate. Portraits remain in the private workspace.
- **Reading page and RSS:** publish English or bilingual paper content without exposing interest portraits, library history, or private judgment explanations.
- **Independent execution:** the website manages accounts and configuration; configured GitHub Actions schedules continue without the website being online.

## Get started

1. Open [ZotWatch](https://zotwatch.yorks0n.com/) and sign in with GitHub.
2. Follow the GitHub app installation and authorization steps, then create a workspace.
3. Connect Zotero and authorize read access to your library.
4. Wait for the initial sync, model construction, and recommendation run. Initial runs usually take longer than incremental runs.
5. Open your results and subscribe to your generated RSS URL in Zotero or another reader.

Setup uses a **private computation repository and a separate public results repository**. You do not need to fork this engine or deploy the public harvesting service.

Use the website to configure the daily schedule and optional AI connections. Translation and judgment are independent; Jev judgment still needs an available LLM connection for interest portrait generation. Subscribe to the URL returned for your own workspace, not a developer test feed.

## Recommendation and privacy boundaries

Optional AI runs after the original recommendation list. Judgment failures retain that list; translation failures retain English text. Failures are not presented as quality judgments.

An insufficient library, lack of qualified interest centers, sparse candidates, or unavailable sources can result in a not-ready state, fewer recommendations, or no results. The engine does not silently relax its model requirements to fill the list. Abstract-based judgment is not a substitute for reading a paper.

When private-context AI is enabled, the selected service receives bounded historical paper excerpts or interest descriptions. These are excluded from public RSS. See [interest portraits](docs/CENTER_PORTRAITS_V1.md) and [optional AI](docs/P5C9_LOCAL_USAGE.md) for details.

## Components

| Component | Responsibility |
| --- | --- |
| [ZotWatch](https://github.com/Yorks0n/ZotWatch), this repository | Python engine, model lifecycle, contracts, reusable workflows, RSS publisher |
| [zotwatch-web](https://github.com/Yorks0n/zotwatch-web) | Web frontend and Cloudflare Worker for authorization, setup, configuration, and run management; private source |
| [zotwatch-workspace-template](https://github.com/Yorks0n/zotwatch-workspace-template) | Versioned workspace templates; private source |
| [zotwatch-public-harvester](https://github.com/Yorks0n/zotwatch-public-harvester) | Public literature metadata collection, deduplication, storage, and APIs |
| Each user's private workspace | Configuration, credentials, scheduled execution, and private state |
| Each user's public results repository | Paper reading page and RSS |

The website configures the private workspace. That workspace runs the engine against Zotero and the public candidate API, then sends only approved output to the public results repository.

## Local development

Python 3.11+ is required. Install from the checked-out source; this does not imply a stable PyPI release:

```sh
git clone https://github.com/Yorks0n/ZotWatch.git
cd ZotWatch
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[latent]'
zotwatch --help
```

Keep personal configuration, credentials, and state in a separate workspace. Validate an existing `zotwatch.yaml` offline:

```sh
zotwatch config validate --workspace /path/to/private-workspace
```

Validation does not establish credential, model-cache, or service readiness. Automatic recommendations require a private repository identity and a pinned encoder, among other runtime inputs. Prefer the website-provisioned workflow; legacy `watch --rss` flags are not interchangeable with `latent-auto-v1` configuration.

See [local optional AI usage](docs/P5C9_LOCAL_USAGE.md), the [disabled-by-default example](examples/optional-llm.yaml), and the [legacy fork/local guide](docs/legacy-usage.en.md).

### Tests and builds

```sh
python -m pip install -r tests/requirements-latent.txt -r tests/packaging-requirements.txt
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m pytest -c tests/pytest.ini -q
python -m build --no-isolation
```

Offline tests use synthetic data. Installation tests require dedicated wheel/editable environments supplied by packaging CI. Local validation does not establish production end-to-end acceptance.

## Versions and upgrades

This branch consolidates ZotWatch 2.x development. The Python package remains `2.0.0.dev1`. Website deployments have been recorded, but consolidating source does not itself publish a stable release or migrate existing users.

Personal callers pin full engine commit SHAs. Supported-engine contracts, setup templates, and publisher scripts must be upgraded together. Historical `P5C*` documents and workflow names containing `staging` are retained for traceability. See [development notes](docs/V2_DEVELOPMENT.md) for scope and validation.

## Layout

```text
zotwatch/           CLI, configuration, contracts, interests, optional AI
src/                Sync, embeddings, candidates, ranking, legacy compatibility
scripts/            RSS publishing, diagnostics, development tools
.github/workflows/  Reusable workflows and CI
examples/           Version-specific caller and configuration examples
tests/              Offline, contract, installation, and workflow tests
docs/               Contracts, feature guides, historical acceptance records
```

## License

[MIT](LICENSE)
