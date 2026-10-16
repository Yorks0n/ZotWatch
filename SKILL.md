---
name: zotwatch-local-recommendations
description: 为 Grok Bot、OpenAI Dot 或类似 Agent 配置本地 ZotWatch：读取 Zotero 个人文库，定时执行向量嵌入、兴趣聚类和新论文候选计算，由 Agent 自行筛选、可选翻译，并按用户时间推送文献推荐。
---

# ZotWatch 本地文献推荐

本文件位于 **ZotWatch Python 引擎仓库根目录**，不是 Zotero 桌面程序的数据目录。适用于能够执行本地命令、持久保存私有状态、定时唤醒并向用户发消息的 Agent。只要求编写或说明配置时，不要实际创建密钥、安装定时任务或发送消息；用户要求启用服务时，完成以下配置和验收。

目标流程：Zotero 私有文库 → 本地嵌入与兴趣聚类 → 公共 API 新论文 → 相同编码器嵌入与兴趣中心召回 → Agent 判断、可选翻译 → 定时私信。

**默认直接在 Agent 所在电脑完成安装、配置、计算、状态保存和调度，不要求访问 ZotWatch 网站、注册 GitHub 账号或创建远端工作空间。** 联网用于获取源码/依赖/模型、同步 Zotero、读取公共论文 API 及消息交付；Agent 自身的推理是否经过云端取决于所用平台，因此本地运行不等于完全离线。

**暂不配置仓库内的 LLM、JEV、AI 翻译或发布处理。** Agent 使用自身能力评估初步候选；不要求用户额外提供这些服务的 API Key，也不自动公开 RSS 或创建公开结果仓库。

## 1. 获取 Zotero 个人文库凭据

1. 引导用户在浏览器打开 [Zotero Security / Applications](https://www.zotero.org/settings/security#applications)，使用自己的账号登录；登录和二次验证由用户完成。
2. 在 API Keys / Applications 区域找到个人 **user ID**。它是数字 ID，不是用户名、邮箱、group ID 或文献条目的 key。
3. 选择 **Create new private key / Create New API Key**（以当前页面文案为准），命名为便于识别的名称，例如 `ZotWatch Local Agent`。保持页面的默认权限创建，核对其中包含读取个人 Library 的权限；如果默认未选中，补选个人文库读取。此流程只需元数据读取，不主动增加写入、群组或附件下载权限。
4. 将生成的 private API key 安全保存到本机私有工作空间的 `.env` 或系统凭据管理器。不要要求用户把 key 粘贴到公开聊天、Git 提交或任务描述中；不要在日志或验证输出中打印它。可在同一设置页撤销此专用 key。
5. 先在 Zotero 桌面客户端完成同步；云端 API 只能读到已同步到 zotero.org 的条目。

官方依据：[Web API v3 身份、认证与读取接口](https://www.zotero.org/support/dev/web_api/v3/basics)。个人文库接口为 `https://api.zotero.org/users/{userID}/items`；私有 key 放在 `Zotero-API-Key` 请求头，不放进 URL。

## 2. 准备本地引擎与独立工作空间

使用 Python 3.11+。在引擎根目录执行，已有安装则复用其虚拟环境；记录实际 checkout 的提交版本：

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[latent]'
.venv/bin/zotwatch --help
git rev-parse HEAD
```

若尚无源码，先克隆 `https://github.com/Yorks0n/ZotWatch.git`。不要在定时任务中自动拉取未验证的新版本。

为用户建立独立、持久的私有目录，例如 `~/.local/share/zotwatch-agent`，与引擎源码分开。下文 `ENGINE` 和 `WORKSPACE` 表示它们的**实际绝对路径**。凭据文件权限设为 `0600`，目录限当前用户访问；不将 `.env`、`data/`、模型或兴趣画像放入公开仓库。

### 本地工作空间标识（兼容当前引擎）

当前 `latent-auto-v1` 的字段名称沿用 `ZOTWATCH_WORKSPACE_REPOSITORY_ID`，但本地 `profile` / `watch` 路径只使用它作为正整数状态归属标识，并不查询 GitHub 仓库或要求 GitHub token。对新建的纯本地工作空间，由 Agent 在本机生成一次随机正整数并持久保存到 `.env`，无需用户提供仓库 ID。例如使用安装好的 Python 执行：

```sh
.venv/bin/python -c 'import secrets; print(secrets.randbelow(2**52 - 1) + 1)'
```

将输出作为该工作空间的固定本地 ID；已有 ID 时复用，不每次重新生成，不在多个用户间共用。它不是 Zotero user ID，也不代表经过验证的 GitHub 身份。模型与元数据缓存绑定此值，备份或迁移本地目录时需一并保留。

这是当前字段的本地兼容用法，不是远端仓库授权。本流程不调用 GitHub 状态传输的 `restore`、`publish`、`rollback`；下文的 `encoder` 子命令仅下载通用模型，不需要 GitHub 身份。如果以后用户主动选择远端托管，应按远端流程验证真实仓库身份并单独迁移状态，不能把本地 ID 当成 GitHub 仓库 ID 上传。

私有 `.env` 中保存以下三个值，用实际值替换说明文字，不将它们写进 skill：

```dotenv
ZOTERO_USER_ID=个人数字ID
ZOTERO_API_KEY=个人private_API_key
ZOTWATCH_WORKSPACE_REPOSITORY_ID=Agent生成并持久保存的本地正整数ID
```

CLI 会读取 `--workspace` 下的 `.env`；注意运行环境中同名变量可能优先于文件，尤其不要继承其他仓库的 `GITHUB_REPOSITORY_ID`（它优先于上述本地 ID）。

### 写入 `WORKSPACE/zotwatch.yaml`

```yaml
schema_version: 2
zotero:
  library_type: user
candidates:
  provider: public-api-v1
  sources: [crossref, arxiv, biorxiv]
  window_days: 7
ranking:
  policy: latent-auto-v1
  top_n: 30
  max_preprint_ratio: 0.3
embedding:
  provider: local
  model: sentence-transformers/all-MiniLM-L6-v2
ai:
  services: {}
  features:
    rerank: {enabled: false}
    summary: {enabled: false}
outputs:
  formats: [json]
  publish: false
```

这是初筛池，`top_n` 当前只接受 20 或 30；最终推送可以更少。不要添加 `--rss`、`--report`、`--top` 或 `--push` 到该策略的命令，它们不受支持。不要把旧版 `src.cli` 或 `interests build` 的实验聚类结果混入此运行链。

如果工作空间已有独立 `optional-llm.yaml`，保持 `features.translation.enabled: false`、`features.shadow.enabled: false`、`features.shadow.provider: none`、`allow_private_context: false`；不调用可选 AI 后处理或 JEV 脚本。

## 3. 首次运行和日常候选计算

下列 shell 示例先将两个路径改为实际绝对路径：

```sh
ENGINE='/absolute/path/to/ZotWatch'
WORKSPACE='/absolute/path/to/private-workspace'

# 在本地任务的子 shell 中清除外部身份，改由工作空间 .env 加载固定本地 ID。
unset GITHUB_REPOSITORY_ID ZOTWATCH_WORKSPACE_REPOSITORY_ID

"$ENGINE/.venv/bin/zotwatch" config validate --workspace "$WORKSPACE"
"$ENGINE/.venv/bin/zotwatch" runtime preflight --workspace "$WORKSPACE" --verify-zotero --json
"$ENGINE/.venv/bin/python" -m zotwatch.interests.lifecycle_transport encoder --cache "$WORKSPACE/encoder-cache"
"$ENGINE/.venv/bin/zotwatch" profile --workspace "$WORKSPACE" --latent-encoder-cache "$WORKSPACE/encoder-cache" --machine-result
"$ENGINE/.venv/bin/zotwatch" watch --workspace "$WORKSPACE" --latent-encoder-cache "$WORKSPACE/encoder-cache" --machine-result
```

逐条检查成功后再继续；下载编码器只需在首次安装、缓存缺失或受控升级时进行。引擎通过 `zotwatch/resources/latent-encoder-v1.json` 固定模型 revision 和文件校验值，不能只下载任意同名 MiniLM 模型代替。

日常只需定期执行上述 **`watch`**：它会验证并同步 Zotero、检查模型生命周期、调用新论文 API、嵌入候选、计算中心余弦相似度、排除已收藏文献，并按日期、中心多样性及预印本比例生成初步候选。模型首次自动构建；后续沿用月度与实质变化门槛并复用向量，不要求每日强制全量重建。用户明确需要强制重建时才使用 `profile --full`。当前正式链使用 spherical 兴趣中心构建，不是单独 `interests build` 的 HDBSCAN 实验链。

默认状态位于 `WORKSPACE/data/`：文库镜像 `profile.sqlite`、模型 `latent-state-v1/`，每次候选结果为 `runs/latent-auto-<run_id>.json`。保留这些状态和编码器缓存，否则下次将重新同步或建模。不要复用其他用户的文库和模型。

**必须读取机器结果的 `status`，不能仅看进程退出码。** `not_ready` 也可能返回退出码 0。只有本次 `command == "watch"`、`status == "succeeded"` 的 `recommendations` 才进入 Agent 筛选；以本次返回的 `run_id` 读取对应文件，不随手读取旧的“最新成功”文件。`failed`、`degraded`、`not_ready` 应保留诊断并提示阻塞，不能把它们说成“今天没有新论文”。文库过少、摘要不足或无合格中心时，不擅自调低阈值或编造推荐。

### 新论文 API 与摘要补取

公共候选 API 是论文池，不是个性化推荐 API。默认连接由 `zotwatch/config/public_candidates.py` 的 `load_public_candidate_connection()` 加载，资源在 `zotwatch/resources/public-candidates-v1.json`；复用其中 `base_url` 和 publishable key，**不要把 Zotero private key 发给该服务**。通常直接让 `watch` 使用现有客户端即可，无须自行重复下载候选池或部署采集服务。

需要补取元数据或诊断时，请求头使用 `apikey: <connection.publishable_key>`，URL 为连接的 `base_url` 加下列路径（默认 base URL 已包含 `/functions/v1`）：

| 路径 | 用途与参数 |
| --- | --- |
| `/public-candidates-v1` | 近期论文；`since`、`until`、`sources`、`limit`、`offset` |
| `/public-candidates-incremental-v1` | 增量变更；必填 `updated_since`，支持 `limit`、`offset` |
| `/public-work-v1` | 单篇完整元数据；`doi` 或 `id` 二选一 |
| `/public-status-v1` | 检查来源采集状态与 freshness |

列表响应使用 `data` 和 `paging.next_offset`；如自行增量抓取，应完整翻页、按 DOI/规范 work identity 去重，全部成功持久化后才推进游标，保留重叠窗口以接收迟到更新。失败不推进游标，尊重限流与退避。

`recommendations` 包含 `title`、`venue`、`doi`、`url`、`published`、`source`、`identifier`、`work_key`、`primary_center_id`、`center_cosine` 等，**不包含摘要**。先按 DOI 向 `/public-work-v1` 补取；无 DOI 时只使用已知真实 API work UUID 作为 `id`，不要将内部 `work_key` 当成 UUID。未命中则从论文 DOI 页、出版社或相应预印本来源核实摘要。不要从标题推造研究结果。

## 4. Agent 自行判断与可选翻译

将 `recommendations` 当作初步候选，不直接全量推送。结合用户明确表达的近期研究问题、文库中的代表性论文、相近兴趣中心，以及候选标题和真实摘要，判断研究对象、方法、问题和结论是否值得用户阅读。语义相似度仅表示相关性，不代表论文质量，也不因期刊名气直接判定推荐价值。

优先保留与当前项目相关、有方法借鉴价值或提供可信新发现的论文；控制同主题重复，允许少量有明确关联的跨领域探索。默认每次选 3–5 篇，可按用户偏好调整，宁缺毋滥。用户的近期明确兴趣优先于陈旧收藏推断。将相关性判断与论文作者的结论分开表达。

用户偏好中文时，由 Agent 可选翻译标题并写中文简介，同时保留原题、专有名词与 DOI。摘要缺失时注明“未获得摘要，仅能依据标题判断”，不要给出不存在的实验结果；依据不足的候选可以不推荐。预印本明确标注，不称作已同行评审的期刊论文。

## 5. 在 Agent 本机创建定时任务并交付

首次启用时记录：用户时区（IANA 名称）、每日发送时间、消息渠道及接收者、输出语言、篇数和关注方向。时间未指定可默认 **用户当地中午 12:00**；不要默认使用服务器 UTC。消息渠道只使用用户已授权向本人推送的渠道；缺少渠道或时区时补齐这些信息，不重复询问已有授权。

优先使用 Agent 原生的本地定时唤醒能力；否则使用 macOS `launchd`、Linux `systemd timer` / cron 或 Windows Task Scheduler。选择当前机器实际支持的一种，创建后读回并验证时间、时区、任务路径和启用状态。任务必须在本机执行上述 Python 环境和持久工作空间，无需 GitHub Actions、ZotWatch 网站或服务器部署。若用户已有其他定时推荐，先明确交付分工，避免双重推送。

- **计算与准备任务**：例如每天 11:00，运行 `watch`，检查本次结果，补齐摘要，唤醒 Agent 完成筛选和可选翻译，将最终消息原子写入私有待发队列。首轮建模应在启用日先完成，并依据实测耗时调整提前量。
- **交付任务**：用户选定时间（默认 12:00）读取当日已完成队列，通过 Agent 的已授权消息工具发送给用户；成功后记录发送时间、消息 ID 和论文 identity。不能把 cron 运行成功或 JSON 文件生成等同于已推送。
- **调度实现要求**：shell 调度器本身不会进行 Agent 判断或发送消息；必须连接目标 Agent 确实存在的 CLI/唤醒接口及消息通道，不编造 `grok`、`dot` 等命令。如果平台无法被定时唤醒或无法推送，应说明尚未完成自动交付，而非留下不可执行的任务。
- 使用虚拟环境可执行文件和工作空间的绝对路径，不依赖交互式 shell 激活、当前目录或桌面登录环境；阻止同一工作空间并发运行，保存私有日志并避免记录凭据。
- 持久记录已发送的 DOI/规范 identity，跨日去重；仅在发送确认成功后标记已发送。发送结果不明确时先查回执，避免盲目重发。失败的待发项保留重试。
- 电脑休眠或离线不能保证准点。支持时启用唤醒/错过后补跑，并去重；迟到交付注明日期，避免连续补发多天旧推荐。准备未完成时不要悄悄发送旧队列。
- 无值得推荐的新论文时默认不打扰；需要用户处理的凭据失效、模型未就绪或持续失败才通知。用户要求每日无结果回执时按其偏好执行。

推荐消息每篇至少包含**标题、期刊与简要介绍**，并给出可打开的文献链接。例如：

```text
今日文献推荐 · YYYY-MM-DD

1. 原文标题（可选中文译名）
期刊：真实期刊名称｜发表日期；如为预印本注明平台与状态
简介：用 2–3 句概括研究问题、方法与主要发现，以实际摘要为依据。
推荐理由：用 1 句说明与用户当前研究兴趣的关系。
链接：DOI 或论文原始页面
```

期刊信息缺失就写“期刊信息未提供”，不以数据来源 Crossref 代替期刊。仅向该用户交付其个性化推荐；公开论文信息与私人文库、兴趣中心明细分开处理。

## 6. 启用验收与后续维护

完成一次手动端到端检查：配置合法、Zotero 验证通过、编码器完整、模型就绪、当前 `watch` 结果成功、摘要可追溯、推荐格式完整、队列去重有效。按用户授权完成消息通道测试，再确认两个定时任务的下次执行时间。没有实际执行的部分明确标记“未验证”。本 skill 的编写本身不代表这些步骤已经执行。

向用户报告配置所在位置（不含 key）、本地任务名称、时区、计算/发送时间、消息渠道和如何暂停。用户要求暂停时禁用对应定时任务；要求撤销文库访问时引导到 Zotero 同一密钥页面撤销专用 key。

实现入口供排错：[`zotwatch/cli.py`](zotwatch/cli.py)、[`auto_runner.py`](zotwatch/interests/auto_runner.py)、[`lifecycle.py`](zotwatch/interests/lifecycle.py)、[`auto_results.py`](zotwatch/interests/auto_results.py)。当前版本行为以这些源码和 [`README.md`](README.md) 为准；历史阶段文档中的测试账号、部署 ID 与 feed 不是用户配置。
