# ZotWatcher (Zotero + Web APIs) — 设计与实现规范

## 目标

我计划使用Zotero Web API获取文库信息，首次全量获取并构建兴趣画像，后续每周增量获取数据库并重建兴趣画像，使用GitHub action定期运行，每天凌晨获取各个网站新发表的文章信息并计算兴趣程度，并将候选的感兴趣文章生成RSS以供订阅。

- **数据源**：用 **Zotero Web API** 首次全量获取文库，构建“兴趣画像”；之后**每周**增量获取（增量同步）并**重建画像**。
- **监测**：**每天凌晨**拉取 OpenAlex / Crossref / arXiv / bioRxiv（可选 Altmetric）近新文章，计算与兴趣画像的相关度，**去重并打分**。
- **输出**：生成 RSS（可选 HTML/Markdown 报告）；可选回写“AI Suggested”集合到 Zotero。
- **运行**：由 **GitHub Actions** 定时运行；同时支持本地开发/调试。

------

## 架构概览

```
Zotero ↔ Zotero Web API
          │
          ▼                     ┌───────────────┐
   ingest_zotero_api.py  ───►   │ profile store │  (profile.sqlite + faiss.index + profile.json)
          │                     └───────────────┘
          │ full (首次) / weekly rebuild
          ▼
    build_profile.py  ──>  (向量化, 聚类/兴趣中心, 高频词, 作者/期刊画像、白名单)
          │
          │ daily fetch
          ▼
 fetch_new.py (OpenAlex/Crossref/arXiv/bioRxiv [+Altmetric])
          │
       dedupe.py
          │
      score_rank.py (相似度 + 时效 + 引用 + Altmetric + 作者/期刊命中)
          │
          ├── report_html/md.py（可选）
          └── rss_writer.py  →  reports/feed.xml  (供订阅/Pages发布)

（可选）push_to_zotero.py → 创建集合+写入候选（含推荐说明 Note）
```

------

## 技术栈

- **Python 3.11+**：`requests`, `pydantic`, `pandas`, `numpy`, `rapidfuzz`, `jinja2`
- **向量化**：`sentence-transformers`（默认 `all-MiniLM-L6-v2`；可换更强模型）
- **向量检索**：`faiss-cpu`（或 `hnswlib`）
- **持久化**：`sqlite3`（`profile.sqlite`），二进制 `faiss.index`
- **CI/CD**：GitHub Actions（定时任务 + GitHub Pages 发布 RSS/报告）

------

## 仓库结构

```
.
├─ README.md
├─ requirements.txt
├─ src/
│  ├─ cli.py
│  ├─ ingest_zotero_api.py
│  ├─ build_profile.py
│  ├─ fetch_new.py
│  ├─ dedupe.py
│  ├─ score_rank.py
│  ├─ report_html.py           # 可选
│  ├─ rss_writer.py
│  └─ push_to_zotero.py        # 可选
├─ config/
│  ├─ zotero.yaml
│  ├─ sources.yaml
│  └─ scoring.yaml
├─ data/
│  ├─ cache/                   # 分页游标与API缓存
│  ├─ profile.sqlite
│  ├─ faiss.index
│  └─ profile.json
├─ reports/
│  ├─ feed.xml
│  └─ weekly-YYYYWW.html       # 可选
└─ .github/workflows/
   ├─ weekly_profile.yml
   └─ daily_watch.yml
```

------

## 配置文件

### `config/zotero.yaml`

```
mode: "api"     # 目前使用 Web API。若后续想支持 BBT 可扩展为 "bbt"
api:
  user_id: "${ZOTERO_USER_ID}"        # 从 GitHub Secrets 或 .env 注入
  api_key_env: "ZOTERO_API_KEY"       # 环境变量名
  page_size: 100
  polite_delay_ms: 200                # 分页间隔，减小限流风险
```

### `config/sources.yaml`

```
window_days: 30
openalex:
  enabled: true
  mailto: "you@example.com"
crossref:
  enabled: true
  mailto: "you@example.com"
arxiv:
  enabled: true
  categories: ["q-bio.GN", "cs.LG"]
biorxiv:
  enabled: true
  from_days_ago: 30
medrxiv:
  enabled: false
altmetric:
  enabled: false
  api_key_env: "ALTMETRIC_KEY"
```

### `config/scoring.yaml`

```
weights:
  similarity: 0.55
  recency: 0.15
  citations: 0.15
  altmetric: 0.10
  author_venue: 0.05
thresholds:
  must_read: 0.75
  consider: 0.55
dedupe:
  title_similarity: 0.90
recency_decay:
  days_full: 30
  days_half: 60
  days_min_score: 180
author_venue_bonus:
  author_hit: 0.05
  venue_hit: 0.03
```

------

## 环境变量与密钥

- `ZOTERO_API_KEY`（**必需**，写权限可选；只读即可构画像与拉库，写候选需写权限）
- `ZOTERO_USER_ID`（**必需**）
- `ALTMETRIC_KEY`（可选）
- `OPENALEX_MAILTO` / `CROSSREF_MAILTO`（也可写到 `sources.yaml`）

> GitHub Actions 中通过 `Settings → Secrets and variables → Actions` 添加；本地用 `.env` 或 shell 导出。

------

## 关键流程规范

### 1) 首次全量获取（构建画像）

- `ingest_zotero_api.py`
  - `GET /users/{userID}/items?limit=...` 分页抓取；
  - 记录响应头 `Last-Modified-Version`（库版本号）为 `data/cache/library.version`；
  - 解析字段：`title, abstractNote, creators, publicationTitle, date, DOI, url, tags, collections`；
  - 生成“内容哈希”（标题+摘要+标签+作者）存入 `profile.sqlite`，字段级 schema 见后文。
- `build_profile.py`
  - 将（标题+摘要+关键词）进行向量化，保存向量；
  - 建立 FAISS/HNSW 索引；
  - 统计高频词（TF/TF-IDF）、常见作者/期刊（Top-N），得到**兴趣词表**与**作者/期刊白名单**；
  - 计算兴趣中心向量（加权平均或聚类中心），保存为 `data/profile.json`。

### 2) 每周增量 + 重建

- `ingest_zotero_api.py` 增量：
  - 读取 `library.version`，发送 `If-Modified-Since-Version: <ver>`；
  - 解析变更，**仅对新增/修改**条目更新哈希与向量；
  - `GET /users/{userID}/deleted?since=<ver>` 处理删除墓碑；
  - 更新 `library.version`。
- `build_profile.py` 做**轻重分离**：
  - 轻：仅更新受影响条目，局部重算关键词/白名单；
  - 重：**每周**（由 workflow 触发）做一次全量重聚类与中心更新，写回 `profile.json` 与 `faiss.index`。

### 3) 每日抓新 + 打分

- `fetch_new.py`：按 `window_days` 抓近新文章，标准化：
  - OpenAlex：`/works?search=...&from_publication_date=...`，带 `mailto=` 参数；
  - Crossref：`/works?filter=from-pub-date:...,until-pub-date:...&query=...`；
  - arXiv：按分类与日期窗口拉 Atom/JSON；
  - bioRxiv：按日期窗口拉 JSON；
  - 保存分页游标/最后抓取时间到 `data/cache/`。
- `dedupe.py`：按 DOI/ID 去重，标题近似阈值见 `scoring.yaml`。
- `score_rank.py`：
  - 计算候选向量，与**兴趣中心**/各簇中心最大余弦相似；
  - **时效因子**：≤30 天=1.0、60 天=0.5、180 天=0.2；
  - `log1p(citations)`、Altmetric 归一化；
  - 作者/期刊白名单命中加分；
  - 输出 JSON（含推荐理由摘要：命中关键词、相似度 top-terms 等）。

### 4) 生成 RSS（与可选报告/回写）

- `rss_writer.py`：输出 `reports/feed.xml`（只保留最近 50–200 条，稳定 `guid`：`doi:`/`arxiv:`/hash）。
- （可选）`report_html.py`：Jinja2 渲染 HTML/Markdown。
- （可选）`push_to_zotero.py`：创建集合“AI Suggested”，按 DOI 查重后写入条目与 Note（推荐说明）。

------

## 数据库与文件格式

### `profile.sqlite`（建议 schema）

- `items(item_key TEXT PRIMARY KEY, doi TEXT, title TEXT, abstract TEXT, authors TEXT, venue TEXT, year INT, tags TEXT, collections TEXT, content_hash TEXT, updated_at TEXT)`
- `embeddings(item_key TEXT PRIMARY KEY, vector BLOB)`
- `stats(key TEXT PRIMARY KEY, value TEXT)`  # 存库版本号、上次构建时间等
- `authors_count(name TEXT PRIMARY KEY, cnt INT)`
- `venues_count(name TEXT PRIMARY KEY, cnt INT)`
- `terms_count(term TEXT PRIMARY KEY, cnt INT)`

### `profile.json`（兴趣画像）

```
{
  "library_version": 12345,
  "center_vector": "<binary-base64-or-file-ref>",
  "top_terms": ["single-cell","ONT","isoform","Arabidopsis", "..."],
  "author_whitelist": ["Smith J", "Watanabe K", "..."],
  "venue_whitelist": ["Nature Methods","Genome Biology","bioRxiv"],
  "built_at": "2025-10-29T01:23:45Z"
}
```

------

## CLI 约定

```
# 首次全量 + 画像构建
python -m src.cli profile --full

# 周更（增量 + 重建）
python -m src.cli profile --weekly

# 每日：抓新 + 去重 + 打分 + 产出RSS
python -m src.cli watch --rss --top 100

# 全流程（供本地一次性跑）
python -m src.cli full
```

------

## 代码骨架（片段）

### `requirements.txt`

```
requests
pydantic
pandas
numpy
rapidfuzz
sentence-transformers
faiss-cpu
jinja2
```

### `src/rss_writer.py`（核心片段）

```
from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
import hashlib, pathlib

def make_guid(rec):
    if rec.get("doi"): return f"doi:{rec['doi']}"
    if rec.get("arxiv_id"): return f"arxiv:{rec['arxiv_id']}"
    basis = (rec.get("title","") + "|" + "|".join(rec.get("authors",[])) + "|" + (rec.get("published","") or "")).encode()
    return "hash:" + hashlib.sha1(basis).hexdigest()

def to_rfc2822(dt_iso):
    if not dt_iso:
        return format_datetime(datetime.now(timezone.utc))
    return format_datetime(datetime.fromisoformat(dt_iso.replace("Z","+00:00")).astimezone(timezone.utc))

def generate_rss(recs, out_path="reports/feed.xml", title="AI Suggested Papers",
                 link="https://example.org/ai-suggested", desc="Personalized recommendations"):
    now = format_datetime(datetime.now(timezone.utc))
    items = []
    for r in recs:
        items.append(f"""
        <item>
          <title>{escape(r.get('title','(no title)'))}</title>
          <link>{escape(r.get('link',''))}</link>
          <guid isPermaLink="false">{escape(make_guid(r))}</guid>
          <pubDate>{to_rfc2822(r.get('published'))}</pubDate>
          <description><![CDATA[
            <p><strong>Score:</strong> {r.get('score',0):.2f} • <strong>Venue:</strong> {escape(r.get('venue',''))}</p>
            <p><strong>Authors:</strong> {escape(", ".join(r.get("authors", [])))}</p>
            <p>{escape((r.get("summary") or "")[:800])}</p>
          ]]></description>
        </item>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>{escape(title)}</title><link>{escape(link)}</link>
<description>{escape(desc)}</description><language>en</language>
<lastBuildDate>{now}</lastBuildDate>
{''.join(items)}
</channel></rss>"""
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(out_path).write_text(xml, encoding="utf-8")
    return out_path
```

> 其余模块让 codex 按“流程规范”与“配置”生成即可（抓 API、增量逻辑、FAISS 索引、评分等）。

------

## GitHub Actions 工作流

### 1) 每周重建画像：`.github/workflows/weekly_profile.yml`

```
name: Weekly Profile Rebuild
on:
  schedule:
    - cron: "0 2 * * 1"   # 每周一 02:00 UTC
  workflow_dispatch:

jobs:
  rebuild:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      ZOTERO_API_KEY: ${{ secrets.ZOTERO_API_KEY }}
      ZOTERO_USER_ID: ${{ secrets.ZOTERO_USER_ID }}
      ALTMETRIC_KEY: ${{ secrets.ALTMETRIC_KEY }}
      OPENALEX_MAILTO: you@example.com
      CROSSREF_MAILTO: you@example.com
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: python -m src.cli profile --weekly
      - name: Commit artifacts
        run: |
          git config user.name "gh-actions" && git config user.email "actions@github.com"
          git add data/ reports/ || true
          git commit -m "weekly profile rebuild" || true
          git push || true
```

### 2) 每日抓新 + 生成 RSS + 发布 Pages：`.github/workflows/daily_watch.yml`

```
name: Daily Watch & RSS
on:
  schedule:
    - cron: "0 1 * * *"   # 每天 01:00 UTC
  workflow_dispatch:

jobs:
  watch:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pages: write
      id-token: write
    env:
      ZOTERO_API_KEY: ${{ secrets.ZOTERO_API_KEY }}
      ZOTERO_USER_ID: ${{ secrets.ZOTERO_USER_ID }}
      ALTMETRIC_KEY: ${{ secrets.ALTMETRIC_KEY }}
      OPENALEX_MAILTO: you@example.com
      CROSSREF_MAILTO: you@example.com
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: python -m src.cli watch --rss --top 100
      - name: Prepare Pages
        run: |
          mkdir -p public
          cp -r reports/* public/
      - uses: actions/upload-pages-artifact@v3
        with: { path: ./public }
      - uses: actions/deploy-pages@v4
```

> 部署完成后，RSS 订阅地址即为你的 GitHub Pages 路径 `/feed.xml`。

------

## 去重与打分策略（复述）

1. **去重**：DOI → arXiv/bioRxiv ID → 标题近似（`rapidfuzz` 相似度 ≥ 0.90）。
2. **相似度**：候选向量 vs 兴趣中心/簇中心取最大余弦。
3. **时效**：30/60/180 天衰减。
4. **质量/热度**：`log1p(citations)`；Altmetric 归一化（可选）。
5. **作者/期刊命中**：白名单加分。
6. **综合分**：权重见 `scoring.yaml`；分档为 must_read / consider / ignore。

------

## 增量同步（Zotero Web API）要点

- 首次保存响应头 `Last-Modified-Version`；后续请求带 `If-Modified-Since-Version`。
- 删除/回收站通过 `/deleted?since=<ver>` 获取“墓碑”，从画像/索引中移除。
- 条目字段比对：仅当（标题/摘要/作者/标签/集合）哈希变化时重算向量。
- 每周进行全量重聚类，防止簇漂移。

------

## 本地开发

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ZOTERO_API_KEY=xxx
export ZOTERO_USER_ID=yyyy

python -m src.cli profile --full
python -m src.cli watch --rss
```

------

## 安全与合规

- 将 API Key 存放于 Secrets（Actions）或本地环境变量，不要提交到仓库。
- 若报告公开发布，请在描述中**不暴露**内部权重细节或完整白名单（可简化）。
- JIF 等专有指标不做程序化抓取；引用数用 OpenAlex 字段或忽略。

------

## 测试与验收

- 单元：去重、哈希变更检测、相似度、衰减函数、RSS 生成（XML 有效性）。
- 集成：模拟小样库（10–50 篇）→ 首次构建 → 增量修改一条记录 → 验证仅一次向量重算。
- “金丝雀”发布：每天仅推送 top 20 到 RSS，核对链接与摘要，确认无误再扩大阈值/数量。

------

## 可选：回写候选到 Zotero

- 创建集合 “AI Suggested”；POST 条目 JSON（先 GET 按 DOI 查重）。
- Note 中写入“推荐理由”：关键词命中、作者白名单命中、相似度等。
- 用户在客户端用彩色标签 `⭐ Favorite` / `Ignore` 做反馈；你的增量同步会读取并用于下次权重调节。