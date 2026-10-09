# RSS 格式修复已发布

状态（2026-10-09）：用户已明确授权发布。v4 已发布并在原订阅地址生效，原有 staging RSS 自动发布 workflow 已恢复。

## 已修复

1. 页尾使用 `Journal:` / `Original article:`；缺失期刊显示 `Not provided`。
2. `10.1111/tpj.71133`：JATS 源码缩进不再生成 br，保留段落及明确的 br/break 标记。斜体和上下标保持行内，清理源码缩进在括号、逗号和上下标周围留下的空格。纯文本摘要仍保留原始换行。
3. `10.1111/nph.71574`：JATS 的 p 可以包住 list，但 HTML 的 p 不能包住 ul/ol 或另一个 p。转换器在列表/块元素前先关闭 HTML 段落，移除空的外层段落，保留四个 li 的全部文字、斜体和上下标。
4. description 使用 v4 版本标记，允许下次获准发布时一次性更新旧格式。旧版公开元数据不可用时，拆分并复用原有摘要与期刊，避免重复嵌套期刊/链接页尾。

## 验证

- RSS、P5C7、P5C8 相关测试：20 passed。
- 两篇真实 Crossref 摘要的转换前后全文在排除空白后逐字符一致，无内容丢失。
- 本地浏览器检查：第二篇恰有四项；没有 p 内嵌 p/ul/ol 或空段落；两篇均有独立标题及英文页尾。
- 32 条既有 RSS 的离线升级：GUID、title、link、pubDate 和顺序保持一致；new_items=0；重复运行字节不变，不再次读取元数据。离线预览中其余条目复用原有公开摘要，两篇问题摘要使用原始 Crossref 数据。
- [两篇真实摘要的本地预览](previews/rss-formatting-v4.html)。
- 真实已发布 RSS 在标准浏览器中解析为四个列表项，无 p 内嵌 p/ul/ol，包含最后一项及英文页尾；两篇公开摘要全文无丢失。用户自己的 RSS 阅读器尚未直接复验。

## 发布暂停记录

用户上轮叫停前启动的 RSS run 37873371412 已成功，生成 commit c68375d90baeb14629b56dd8c282485984d94ae4（v3）。Pages run 37873441844 取消时返回 409，未赶上取消。随后已禁用 staging 的 RSS workflow；本轮复核页面仍显示 `This workflow was disabled manually.`。当时 v4 尚未发布；该暂停现已按用户授权解除。

## 发布验证（2026-10-09）

- staging publisher：`da1c6bf4fd14578fbf9b0a1b50e9eff7edf12074`；远端内容与本地已验证脚本逐字一致。
- [RSS workflow](https://github.com/ykson-dev/zotwatch-p5a3-staging-20260929/actions/runs/37875340178)：success，32 条、new_items=0；第二次发布 changed=false。
- RSS commit：`b297c543c09d0d0b623788d776c081537c4d8f16`。
- [Pages 部署](https://github.com/Yorks0n/zotwatch-rss-staging/actions/runs/37875395262)：success。
- [原订阅地址](https://yorks0n.github.io/zotwatch-rss-staging/feed.xml) SHA-256：`3cae551c1ab3e7cb6f63ad6a4ab6e2fdb77debf98fbb1a8bdd74945e1604a27e`，与已验证发布文件一致。
- 32 条 title/GUID/link/pubDate 均保持原值；全部 description 为 v4 且各有一个英文期刊/原文链接页尾。
- 自动发布已恢复，workflow 页面重新显示 Run workflow，不再显示 Disabled。
