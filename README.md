# ZotWatcher

ZotWatcher 是一个基于 Zotero 数据构建个人兴趣画像，并持续监测学术信息源的新文献推荐流程。它每日在 GitHub Actions 上运行，将最新候选文章生成 RSS/HTML 报告，必要时也可在本地手动执行。

## 功能概览
- **Zotero 同步**：通过 Zotero Web API 获取文库条目，增量更新本地画像。
- **画像构建**：对条目向量化，提取高频作者/期刊，并记录近期热门期刊。
- **候选抓取**：拉取 Crossref、arXiv、bioRxiv/medRxiv（可选）等数据源，并对热门期刊做额外精准抓取。
- **去重打分**：结合语义相似度、时间衰减、引用/Altmetric、SJR 期刊指标及白名单加分生成推荐列表。
- **输出发布**：生成 `reports/feed.xml` 供 RSS 订阅，并通过 GitHub Pages 发布；同样可生成 HTML 报告或推送回 Zotero。

## 快速开始
1. **克隆仓库并准备环境**
   ```bash
   git clone <your-repo-url>
   cd ZotWatcher
   mamba env create -n ZotWatcher --file requirements.txt  # 或使用 pip 安装
   conda activate ZotWatcher
   ```

2. **配置环境变量**
   在仓库根目录创建 `.env` 或 GitHub Secrets，至少包含：
   - `ZOTERO_API_KEY`：Zotero Web API 访问密钥
   - `ZOTERO_USER_ID`：Zotero 用户 ID（数字）
   可选：
   - `ALTMETRIC_KEY`：用于获取 Altmetric 数据
   - `OPENALEX_MAILTO`/`CROSSREF_MAILTO`：覆盖默认监测邮箱

3. **本地运行**
   ```bash
   # 首次全量画像构建
   python -m src.cli profile --full

   # 日常监测（生成 RSS + HTML）
   python -m src.cli watch --rss --report --top 50
   ```

## GitHub Actions 部署
1. **开启 Git**
   ```bash
   git init
   git add .
   git commit -m "Initial ZotWatcher setup"
   git remote add origin <your-github-repo-url>
   git push -u origin main
   ```

2. **配置 Secrets**（仓库 Settings → Secrets and variables → Actions）
   - `ZOTERO_API_KEY`
   - `ZOTERO_USER_ID`
   - （可选）`ALTMETRIC_KEY` 等

3. **启用 GitHub Pages**
   - Settings → Pages → Source 选择 “GitHub Actions”。

Workflow 文件 `.github/workflows/daily_watch.yml` 中的关键命令：
```yaml
- run: python -m src.cli watch --rss --top 100
```
可根据需求添加 `--report` 等选项。

Workflow 的触发条件：
- 每天 **UTC 06:00** 定时运行
- 当 `main` 分支有新的 push
- 手动 `workflow_dispatch`

## 目录结构
```
├─ src/                   # 主流程模块
├─ config/                # YAML 配置，含 API 及评分权重
├─ data/                  # 画像/缓存/指标文件（不纳入版本控制）
├─ reports/               # 生成的 RSS/HTML 输出
└─ .github/workflows/     # GitHub Actions 配置
```

## 自定义配置
- `config/zotero.yaml`：Zotero API 参数（`user_id` 可写 `$ {ZOTERO_USER_ID}`，将由 `.env`/Secrets 注入）。
- `config/sources.yaml`：各数据源开关、分类、窗口大小（默认 7 天）。
- `config/scoring.yaml`：相似度、期刊质量等权重；并提供手动白名单支持。

## 常见问题
- **缓存过旧**：候选列表默认缓存 12 小时，可删除 `data/cache/candidate_cache.json` 强制刷新。
- **未找到热门期刊补抓**：确保已运行过 `profile --full` 生成 `data/profile.json`。
- **推荐为空**：检查是否所有候选都超出 7 天窗口或预印本比例被限制；可调节 CLI 的 `--top`、`_filter_recent` 的天数或 `max_ratio`。

## 许可证
自定义或遵循所属仓库要求。
