# P5C9 A+B — 本地可选 LLM 服务

两个独立入口，均不接入 `watch`、正式 ranking 或 RSS。默认配置关闭两个功能；英文模式不调用 API。缺 key、服务失败或无效响应时，翻译保留完整英文，Top 20 quality filter 失败时恢复原完整 Top 20；未完成项标 review/uncertain/unclear，不将失败冒充模型判断。

仅依赖已存在的 requests/Pydantic/PyYAML/python-dotenv；复用原 feature budgets、严格配置/YAML loader 和 HTTPS endpoint validator。按用户要求采用通用 API 格式与环境变量，不以供应商区分变量。独立 sidecar 配置避免修改现有 config-v2 契约或启用其历史 summary/rerank 路由。

## 配置

从仓库根目录运行。复制 `examples/optional-llm.yaml` 到 `data/optional-llm/config.yaml`。需要翻译时把 translation 改为 `enabled: true` 并添加 `service: translator`；shadow 使用原兼容 LLM 时独立改为 `enabled: true`、`provider: llm`、`service: judge`。两个 service 的模型可分别设置。

API 格式只区分 OpenAI Chat Completions 与 Anthropic Messages。连接从通用环境变量取得，YAML 的 service 只选择模型（model 或 model_env）。`LLM_TRANSLATION_MODEL` 与 `LLM_SHADOW_MODEL` 未设置时各自回退到 `LLM_MODEL`；也可在 service 中直接指定 `model: your-model`。不会自动挑选或更换模型。

工作目录 `/Users/yorkson/opt/ZotWatch2/.env` 已创建占位配置。填写 key 后，通过 `--env-file ../.env` 从 ZotWatch 仓库显式加载，不输出其内容：

```dotenv
LLM_API_FORMAT=openai
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=actual-local-key
# LLM_TRANSLATION_MODEL=other-model
# LLM_SHADOW_MODEL=other-model
```

官方 OpenAI 通常使用 `LLM_BASE_URL=https://api.openai.com/v1`；DeepSeek 本次使用用户指定的 `https://api.deepseek.com`。其他服务填写其 Chat Completions base URL。Anthropic Messages 格式使用 `LLM_API_FORMAT=anthropic`，BASE_URL 可包含 `/v1` 或使用 API host；客户端按该格式构造 messages/system、x-api-key 与 anthropic-version headers，严格解析 JSON 文本结果。

shadow 必须在 YAML 中显式设置 `allow_private_context: true`。启用后仅发送每个候选的标题、最多 3000 字符摘要，以及该匹配中心最多 3 篇代表文献的标题和每篇最多 900 字符摘要。外发 JSON 不包含 Zotero key、DOI、作者、tags、collections、中心 ID、cosine、文库身份或完整文库；候选仅以批次临时 ID 对齐输出。截断标记随片段发送。结果和本地 cache 含私人兴趣判断，应保留在本地。

base URL 和 key 同来自显式环境连接；YAML 不接受 endpoint/key，避免仅修改 YAML 就将 key 转发到另一站点。只允许经原 validator 校验的 HTTPS endpoint。请求禁止自动重定向；错误输出仅有静态状态码，不记录响应 body、headers、key 或 credential record。占位 `xxxx` / `replace-with-local-key` 视为缺 key，不会发请求。

## 准备真实本地样本

`prepare` 只读取本地 saved evidence；使用原 `AutoRunResult`、`Generation` 和 center model checksum 校验 run/model/library/encoder 一致性，提取已有 representative_items，不生成 LLM topic names、不编码、不重建模型。

```sh
python -m zotwatch.optional_llm prepare \
  --result /private/tmp/p5c8-live/second-result.json \
  --diagnostic /private/tmp/p5c8-live/second-diagnostic.json \
  --metadata /private/tmp/p5c8-live/second-cache.json \
  --generation /private/tmp/p5c7-live/latent-before/latent-state-v1/generations/549d00e7ed254ed19af521ddafcbee36/generation.json \
  --model /private/tmp/p5c7-live/latent-before/latent-state-v1/generations/549d00e7ed254ed19af521ddafcbee36/model.json \
  --output data/optional-llm/input.json
```

仅提取现有 final Top 20，原 center recall、ranking、跨中心选择和 Top 20 顺序保持。公开候选摘要从同一 workspace 的已保存 metadata cache 取得；旧 50 篇 input 也可读取，但 shadow 只判断 final_recommended=true 的原 Top 20，不进行二次排序。

其他运行可使用同格式的本地 input：`translations` 是最终推荐列表，`candidates` 是原 final Top 20，`contexts` 按 primary_center_id 提供代表文献。`prepare` 是现有 P5C8 evidence 的便捷适配器。

## 本地摘要清理

```sh
python -m zotwatch.optional_llm refine-metadata \
  --input data/optional-llm/input.json \
  --output data/optional-llm/refinement-input.json
```

只处理原 Top 20 的可疑摘要：元数据层识别正文/附属声明、超过 6000 字符的未核实文本和纯引用行，重新尝试 DOI 精确匹配的 Crossref → Europe PMC → OpenAlex。失败或没有可信摘要时置空，不截取正文或使用 LLM 生成摘要。写入独立 input，保留 metadata_repairs 来源、原因和尝试记录，不覆盖原 P5C8 cache/result。正常元数据路径也拒绝受污染的旧正缓存；当前只本地修改代码，未部署。

## 独立运行

```sh
python -m zotwatch.optional_llm translate \
  --config data/optional-llm/config.yaml --env-file ../.env \
  --input data/optional-llm/refinement-input.json \
  --output reports/optional-llm/translations.json --mode bilingual

python -m zotwatch.optional_llm shadow \
  --config data/optional-llm/config.yaml --env-file ../.env \
  --input data/optional-llm/refinement-input.json \
  --output reports/optional-llm/quality-filter.json
```

每个命令同时生成同名 `.html`，可直接浏览器打开。英文模式使用 `--mode english`，API 调用为零。默认 cache 为输出目录的 `llm-cache-v1.json`，可通过 `--cache` 单独指定。成功结果按 provider、model、endpoint、完整发送文本/context、prompt version/prompt 的 hash 缓存；失败不缓存；最多 2000 条。改变代表文献片段、论文文本、模型或 prompt 会使对应 cache 失效。输出记录 provider/model/prompt version 和状态，不记录 key。

批量请求直接将若干论文组成一次 Chat Completions JSON 请求，无离线 Batch API 框架。max_items/max_requests/max_output_tokens/timeout 独立受 feature budget 控制。max_input_tokens 使用 UTF-8 字节数作保守上限（可低估容量），超限回退，不静默截断翻译。缓存中超过 6000 字符的源文本需要复核（可能混入正文）：只发送标题及空摘要，源文本完整保留本地，输出明确标记 source_text_requires_review，不生成摘要中译。

OpenAI JSON mode / Anthropic 提示生成 JSON 后均严格校验字段、枚举和输入输出 ID 对应；参考 [OpenAI 官方 JSON mode 文档](https://developers.openai.com/api/docs/guides/structured-outputs)。OpenAI 格式中不支持 JSON mode 的 endpoint 或模型按失败回退，Anthropic 格式中非 JSON 内容同样回退，不隐式切换协议/模型。代码使用标准 OpenAI endpoint 的 max_completion_tokens，其他兼容 endpoint 使用 max_tokens；Anthropic 格式参考 [官方 Messages API](https://platform.claude.com/docs/en/api/messages/create)。实际可用性须以所选模型的真实调用为准。

质量判断返回 decision=keep/review/exclude、relevance=strong/moderate/weak/uncertain、contribution=substantial/incremental/unclear 和≤100字符的具体理由（prompt要求≤80）。只在 20 项全部获得有效判断时模拟剔除 exclude；review 和 keep 均保留，保持原顺序，不补位。如果任一 API 判断失败则整个过滤结果回退原 Top 20，partial exclude 也不执行。不会仅凭期刊、影响因子或 incremental 排除，不把 cosine 与 LLM 分数相加。内容贡献仅依据摘要，不能证明全篇的创新性或研究质量。

默认小批次 2 篇；translation 输出上限 6000、quality 判断 4000 tokens，reason 较短。route 的 retry_once=true 允许对连接/HTTP/输出不完整/无效 JSON 失败重试一次，仍受 max_requests 限制；成功缓存保留，不重新请求成功项。若失败仍未恢复，按上述原 Top 20 回退。旧判断 prompt v1/cache 保留，本次使用 p5c9-top20-quality-v2，避免误用旧 relevance/value 结果。

翻译复用原成功缓存，额外进行 deterministic cleanup：移除冗余开头“摘要”、合并重复结构标题、规范先导编辑（prime editing）与角质素/木栓质术语、统一中英文间距和括号；不修改缓存 raw LLM 译文，cleanup version 独立记录。

JSON/HTML/cache 使用 0600 权限。本地 input/cache/结果及私人质量报告均 Git ignored。命令会拒绝把输出/cache 写回输入文件。输出路径应使用上述独立目录。

## 必要验证

```sh
python -m pytest -c tests/pytest.ini \
  tests/test_optional_llm_p5c9.py \
  tests/test_latent_auto_p5c7.py tests/test_daily_metadata_p5c8.py -q
```

本轮真实样例、20 篇判断和过滤数量见本地 `docs/P5C9A_B_LOCAL_REFINEMENT.md`；旧 50 篇报告保留为历史 `docs/P5C9A_B_LOCAL_REPORT.md`。GitHub Actions、RSS 集成与 production rollout 留待后续单独安排。

## P5C9B：可选 Jev 原生判断

`features.shadow.provider` 选择 `none` / `llm` / `jev`，每次只调用一个推荐判断 provider。`none` 始终原样保留 Top 20，`enabled: false` 也关闭判断。旧配置省略 provider 时仍按原 `llm` 路由运行；新示例默认明确 `none`。翻译开关、服务与缓存保持独立。

`llm` 使用既有 `service: judge` 及 `LLM_*` 连接；`jev` 不需要 service 或 LLM key，仅使用环境变量 `TYPESAFE_API_KEY`、`features.shadow.model`（默认 `jev-latest`）与固定原生 endpoint `https://api.typesafe.ai/v1/systemone`。参见 [TypeSafe 官方 API 文档](https://docs.typesafe.ai/api)。复制 `examples/jev-local.yaml`，将 provider 改为 `jev`，同时显式设置 `allow_private_context: true`，在本地 `.env` 中加入 key（格式见 `examples/jev.env.example`）。切换回 llm 时添加 `service: judge`；服务失败不会切换到另一个 provider。

```sh
python -m zotwatch.optional_llm shadow \
  --config data/optional-llm/jev-config.yaml --env-file ../.env \
  --input data/optional-llm/jev-input.json \
  --output reports/optional-llm/jev/quality-filter.json
```

Jev 使用同一 Top 20、有效摘要及同一历史代表片段，另传公开期刊字段（`venue` 或 `journal`，最多 300 字符）；不以期刊排除。旧 saved input 缺期刊时，可从同一 P5C8 result 补充至独立 input。新 `prepare` 同时提取 saved admitted ranking 的 venue。标题/摘要和代表片段的边界与原 shadow 相同。

原生 API 支持 `choice` 等结构化问题，不生成自由文本理由。本版本每篇只提出一个相关性与贡献的综合选择题，选项为 direct_keep / adjacent_keep / relevance_review / evidence_review / mismatch_exclude / no_contribution_exclude，映射为 keep/review/exclude。输出记录原生选项、完整选项概率、confidence、实际 response_model，以及明确标注的本地规则说明。`relevance`、`contribution` 显示 `not_assessed`，不伪造单独评价或模型解释。概率不与 cosine 相加，也不用于重新排序。所有 review 保留。

Jev 默认缓存 `jev-cache-v1.json`，与现有 LLM 默认缓存文件、schema 和键均独立；键包含 provider、model、endpoint、全部发送输入、规则版本、instructions 与 criteria。只缓存通过验证的成功结果。遇到 429/529/暂时性服务器或连接故障，在开启 retry_once 且预算允许时，等待 2 秒重试一次；401/422/无效选项或概率等错误直接失败。不启动另一模型。原生接口无文档化输出 token 上限参数，因此不发送虚构的 max_tokens；若响应报告的 output_tokens 超预算则拒绝结果。记录请求耗时、实际模型、API 提供的 input/output tokens 和静态错误代码，不保存原始响应或 key。

任意一篇失败时，完整 Top 20 原样保留，成功项仍缓存；下次只请求未成功项。本地对照只读取已保存的 DeepSeek JSON，不再次调用 DeepSeek。报告为 `docs/P5C9B_JEV_LOCAL_REPORT.md`，并保持 Git ignored。必要测试增加 `tests/test_jev_p5c9.py`。

P5C9C staging 使用新增的保守排除门槛：只有 mismatch_exclude 的概率≥0.80、confidence≥0.60、领先次高选项≥0.20且有效摘要≥200字符时剔除；其余原生排除转 review 并保留。原生 choice/native_decision 仍记录，贡献质疑不会单独执行排除。规则版本为 p5c9-jev-conservative-exclusion-v1，native cache 版本更新为 p5c9-jev-joint-quality-v2。此前 Jev 本地报告保留原版本结果，不重写历史判断。

Staging 配置例见 `examples/p5c9c-staging-ai.yaml`，接线和验收见 [P5C9C staging integration](archive/P5C9C_STAGING_INTEGRATION.md)。翻译代码及 DeepSeek quality Prompt 保持已确认版本。
