# ZotWatcher

ZotWatcher 是一个基于 Zotero 数据构建个人兴趣画像，并持续监测学术信息源的新文献推荐流程。它每日在 GitHub Actions 上运行，将最新候选文章生成 RSS/HTML 报告，必要时也可在本地手动执行。

## 功能概览
- **Zotero 同步**：通过 Zotero Web API 获取文库条目，增量更新本地画像。
- **画像构建**：对条目向量化，提取高频作者/期刊，并记录近期热门期刊。
- **候选抓取**：拉取 Crossref、arXiv、bioRxiv/medRxiv（可选）等数据源，并对热门期刊做额外精准抓取。
- **去重打分**：结合语义相似度、时间衰减、引用/Altmetric、SJR 期刊指标及白名单加分生成推荐列表。
- **输出发布**：生成 `reports/feed.xml` 供 RSS 订阅，并通过 GitHub Pages 发布；同样可生成 HTML 报告或推送回 Zotero。

## 快速开始
1. 登录GitHub后，打开仓库页面 [ZotWatch](https://github.com/Yorks0n/ZotWatch)

2. 在顶部点击**Fork**按钮创建分支，将仓库复制到自己的GitHub账号下：**Fork - Create fork**

3. 到fork后的ZotWatch页面，点击设置（**Settings**），在设置页面左侧找到**Secrets and variables**，展开并点击下级的**Action**。
   ![image1](images/image1.png)

4. 点击右侧的**New repository secret**按钮，添加几个必要的Repository secrets
   ![image2](images/image2.png)

5. 添加几个必要的键值对，包括：

   - `ZOTERO_API_KEY`，此为获取 Zotero 数据库中现有个人信息所必须。登录 Zotero 网站的[个人账户](https://www.zotero.org/settings/)后，在 **Settings - Security - Applications** 处点击 **Create new private key**，其中 Personal Library 给予 Allow library access，Default Group Permissions 给予 Read Only 权限，保存获得 API。
   - `ZOTERO_USER_ID`，该 ID 可从上述 **Settings - Security - Applications** 处 **Create new private key** 按钮下方一行 `User ID: Your user ID for use in API calls is ******` 获取。
   -  `OPENALEX_MAILTO`，邮箱地址，用于部分网站 API 请求时的礼貌标注。
   -  `CROSSREF_MAILTO`，邮箱地址，用于部分网站 API 请求时的礼貌标注。
     ![image3](images/image3.png)

6. 回到自己仓库首页，点击顶部**Settings**，在左侧找到**Pages**，在页面中为其**Source**选择**GitHub Actions**，使得生成的RSS页面直接发布到GitHub Pages。

   ![image4](images/image4.png)

7. 接下来点击顶部的**Actions**栏目，并确认开启GitHub Actins
   ![image5](images/image5.png)

8. 点击左侧**Daily Watch & RSS**，默认情况下fork来仓库的Workflow是关闭状态，点击右侧Enable workflow激活。
   ![image6](images/image6.png)

9. 此时仓库理论上会在每天早上六点自动运行，要立刻运行请点击**Run workflow**。首次运行需要全量生成向量数据库，会比较慢，可以点击**All workflows**查看运行状态。

   ![image7](images/image7.png)

10. 运行完后去 **Settings - Pages** 页面上可以看到自己的站点地址，此时直接访问此地址并不能打开，需要复制地址并在末尾加上`/feed.xml`，例如`https://[username].github.io/ZotWatch/feed.xml`，该地址可以导入 Zotero 的 RSS 订阅，或用于导入你喜欢的 RSS 阅读器。
       ![image8](images/image8.png)

## 本地运行
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
   python -m src.cli watch --rss --report --top 20
   ```

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
本项目采用 [MIT License](LICENSE)。
