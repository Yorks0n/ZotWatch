# RSS 摘要排版

RSS description 使用经白名单清洗的 HTML，保留公开摘要中的段落、换行、Abstract/Summary 等已有标题，以及粗体、斜体、上下标和列表。不人为添加缺失的摘要标题。Crossref/Europe PMC 原始排版直接用于 RSS；OpenAlex 仅提供词序时保留其可获得的文本。

摘要后另起段显示 `Journal: …`，再另起段显示 `Original article: …`。期刊取自精确匹配的公开元数据，必要时使用最终结果中的 venue；无法获得时明确写“Not provided”。原文链接沿用 canonical DOI/arXiv 链接。HTML/JATS 只由段落、标题和明确的 br/break 标签决定换行，源码缩进按普通空格处理；纯文本摘要保留原始换行。v4 还会将 JATS 的段落包列表结构转换成合法 HTML，完整保留各个列表项。

旧版 description 首次发布时重新获取公开摘要并更新格式。版本标记使下一次发布直接复用，不重新查询、不重复推送；GUID、标题、链接和首次 RSS 发布时间保持不变。仅格式变化也会正常发布，new_items 仍为 0。

## 验证（2026-10-09）

- 相关 RSS、P5C7、P5C8 测试：17 passed。
- staging publisher commit：`a2aaaf6388bf9d73851016c3e34dd4d44583e30c`。
- [真实 RSS 发布](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37872009893)：success；32 条、new_items=0；重复发布 changed=false。
- RSS-only commit：`d1549fc41555e2ed453854a68fdd787b26e80408`。
- feed SHA-256：`57aed3f177e86c95e6c965ff7e7c4a7b30f16e030fcd5f11942122328898ba91`。
- 全部 32 条保留原 GUID/pubDate/title/link，全部具有期刊与原文链接；31 条具有摘要段落，21 条具有摘要标题，无未知期刊。
- 真实 Journal of Experimental Botany 条目在浏览器中显示独立 Abstract 标题、摘要段落、期刊来源段落、原文链接段落。
- 订阅地址保持 [GitHub Pages feed.xml](https://yorks0n.github.io/zotwatch-rss-staging/feed.xml)。
- [Pages 部署](https://github.com/Yorks0n/zotwatch-rss-staging/actions/runs/37872045441) 已成功；原订阅地址返回新 feed SHA-256，实际托管文件与发布文件完全一致。

## 集中修复（已发布）

v4 修复段内源码换行、非法段落/列表嵌套及英文页尾。20 项相关测试通过；两篇真实摘要全文及浏览器排版已核验。v4 已按用户授权发布，原订阅地址已返回新文件，自动发布已恢复。发布证据见下节。

## v4 发布证据（2026-10-09，历史记录）

- Publisher：`da1c6bf4fd14578fbf9b0a1b50e9eff7edf12074`。
- [RSS workflow](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37875340178)：成功，32 条、new_items=0；第二次发布 changed=false。
- RSS commit：`b297c543c09d0d0b623788d776c081537c4d8f16`；[Pages 部署](https://github.com/Yorks0n/zotwatch-rss-staging/actions/runs/37875395262)成功。
- 当时 feed SHA-256：`3cae551c1ab3e7cb6f63ad6a4ab6e2fdb77debf98fbb1a8bdd74945e1604a27e`。
- 32 条 title/GUID/link/pubDate 保持原值，description 均为 v4；自动发布已恢复。
- 两篇真实摘要在排除空白后全文一致；列表保留四项，无 p 嵌套 p/ul/ol。用户自己的 RSS 阅读器未直接复验。

这些是当次发布的证据，不代表重新验证了当前线上内容。
