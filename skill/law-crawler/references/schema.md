# 元数据 schema 2

每份实际保存的文件均对应元数据 JSON。相同原始字节可关联多个官方来源；来源不同不应被误写成一个没有出处的字段值。

## 顶层结构

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 元数据版本，当前为 `2`。 |
| `document` | 规范化的法规字段；除 `legal_status` 字符串外，每项是带来源的字段对象。 |
| `file` | 本地文件路径、大小、内容哈希、下载时间和媒体类型。 |
| `original_file` | 文本转换时的原始响应文件属性，含原格式、原始 SHA-256、路径和字节数。 |
| `conversion` | 转换方式、原始哈希、来源 URL，以及固定的正文结构识别元数据。 |
| `preferred_source` | 同内容来源的最新明确公布日期选择及未知日期提示。 |
| `sources` | 来源数组，保留各平台详情页及下载链接；每项独立保留该来源的 `metadata`。 |
| `crawl` | 最近检查时间等采集状态。 |
| `body_review` | 显式正文身份审查，绑定原始 SHA，含状态、原因、证据和核验时间；传输成功不自动填核验通过。 |
| `body_review_history` | 显式更正前的审核快照，含原始SHA与状态/原因/证据/时间；缓存、刷新和状态库重建保留，不重复归档同一审核。 |
| `legacy_metadata` | 从旧格式 JSON 迁移时保留的原始记录，不丢弃原有证据。 |

`document` 是汇总视图。分析来源差异时读取 `sources[].metadata`，不能仅凭汇总字段判断所有来源一致。

`body_review.status` 为 `title_type_number_year_match`（限定证据范围内匹配）、`unknown_no_extractable_text_first_two_pages`（扫描/无可提取文字，未确认）、`catalogue_body_type_conflict`（目录与正文类型冲突）或 `not_reviewed`。审核随原字节哈希保留，缓存和同字节来源合并不清除审核；新字节需重新审查。冲突原件保留历史但排除当前原件清单。它不修改目录原始字段，不表示法律效力核验。

状态 `title_type_number_year_match` 可依据明确的视觉核验，不要求文本可提取，也不限于前两页；证据须列明实际正文页范围与方法。疑似隐藏草案字样先比较渲染、内容流及公布/认证页，不能直接按提取关键词判类。审核更正恢复当前候选时保留先前判断和更正理由。未知日期不能覆盖同内容同来源已有observed日期；不同来源/字节不继承。

## 带来源的字段对象

`document` 下的业务字段使用以下结构（`legal_status` 为 `not_verified` 字符串），未提供的信息仍保留该字段：

```json
{
  "value": null,
  "raw": null,
  "source_label": null,
  "source_url": "https://official.example/detail",
  "status": "not_provided",
  "meaning": "此字段的明确业务含义"
}
```

| 属性 | 含义 |
| --- | --- |
| `value` | 规范化值；未提供或无法解析时为 `null`。日期字符串通常规范化为 `YYYY-MM-DD`。 |
| `raw` | 官方页面的原始文本或身份组成字段对象；没有该信息时为 `null`。 |
| `source_label` | 页面中与值对应的字段名称，如 `Tanggal Pengundangan`。 |
| `source_url` | 实际提供该字段的来源页面。 |
| `status` | `observed`：直接观察到并已解析；`not_provided`：页面未提供；`unparsed`：有原始信息但无法可靠规范化；法规身份缺少组成项时可为 `partial`。 |
| `meaning` | 字段究竟指什么，尤其用于区分法规公布、制定、网站发布和采集时间。 |

`observed` 只表示从来源提取到字段，不表示已经完成法律核验。解析失败时保留 `raw`，不猜测日期或用今天补值。

## 法规字段及边界

| 字段 | `value` 类型与解释 |
| --- | --- |
| `title` | 字符串或 `null`；法规详情页标题。 |
| `published_at` | 字符串或 `null`；法规公布日期。贸易部对应 `Tanggal Pengundangan`，明确不是网页发布日期或下载日期。 |
| `updated_at` | 字符串或 `null`；来源明确标注的更新时间，页面未提供时为 `null`。不能用最近检查时间代替。 |
| `effective_region` | 字符串或 `null`；来源明确标注的适用国家/地区。不能仅从中央发布机关、`.go.id` 域名或制定地点推断适用范围。 |
| `enacted_at` | 字符串或 `null`；法规制定日期，对应 `Tanggal Penetapan`。 |
| `promulgated_at` | 字符串或 `null`；法规公布日期，对应 `Tanggal Pengundangan`；在该适配器中与 `published_at` 同义。 |
| `legal_identity` | 对象或 `null`；用于保留国别、发布机关、法规类型、编号、年份等身份信息。 |
| `web_published_at` | 若适配器提供，表示网页发布日期，不能替代法规公布日期。 |

`legal_identity.value` 可包含 `jurisdiction`、`issuer`、`type`、`number`、`year`；成员值为字符串或 `null`。`jurisdiction: "ID"` 表示文献归属印度尼西亚，不自动等同于全国适用。身份信息缺项时保留 `partial`，不从文件名补猜。

贸易部详情中的 `Tempat Penetapan`（如 Jakarta）是制定地点；`Lokasi` 可能是文件保管机构。两者均不能填写进 `effective_region`。法规公布日、制定日也不能替代生效日。

## 文件、来源与采集时间

`file` 包含：

- `path`：相对国家下载目录的主保存文件路径；文本转换时是 Markdown。
- `size_bytes`：主保存文件的实际字节数。
- `sha256`：主保存文件实际字节的 SHA-256，不是标题、URL 或文件名推导的哈希。文本转换后与原响应哈希通常不同。
- `downloaded_at`：该原件下载保存的时间。
- `media_type`：实际主文件媒体类型，PDF 为 `application/pdf`，转换文本为 `text/markdown`。

转换文本另有 `original_file`，记录原始响应的 `path/sha256/size_bytes/media_type/downloaded_at`。原始响应位于 `raw/<原始sha>.<ext>`，其独立审计元数据为 `<原始sha>.<ext>.metadata.json`；主文件为 `<原始sha>.md`，主元数据为 `<原始sha>.json`。

`conversion` 包含 `format: markdown`、`method: structured_text_v1`、`original_sha256`、`source_url` 和 `metadata`。`conversion.metadata` 保存当次转换所用正文结构证据（`document_type`、`text_body_confirmed`、`text_body_selector` 或 `text_body_root` 等带来源字段），用于缓存重新识别。后续选择新的汇总来源不得改写此转换证据；`document` 和 `sources[].metadata` 仍表示各来源业务字段。

`sources` 的每项包含 `platform`、`detail_url`、`download_url`、`canonical_url`、`final_url`、`first_seen_at`、`last_seen_at`、`metadata`，文件请求记录另有 `download_checked_at`。其中：

- `canonical_url` 用于保守的 URL 去重；不能任意删除未知查询参数，因为参数可能决定法规或附件版本。
- `final_url` 是文件请求最终到达的 URL，保留它与原始 `download_url` 的区别。
- `first_seen_at`、`last_seen_at` 是采集证据时间，不是法规公布或更新时间。
- `metadata` 保留该来源提取的业务字段及出处，供追踪来源冲突和后续复核。
- 对已核对的官方 PDF 单文件补收，没有可访问详情时 `detail_url` 为 `null`，可增加 `discovery_method: direct_official_pdf`。身份字段的 `source_url` 指向实际 PDF，不伪造网页字段；`--refresh-metadata` 跳过这类无详情来源。补收报告与自动发现验收结果分开保存。
- 浏览器导入来源标记 `discovery_method: browser_download`。浏览器仅提供下载链接、未提供重定向终点时，`final_url` 暂用请求链接，并明确 `final_url_verified: false`，不能把它解释为已验证终点；只有已实际观察终点时才标为 `true`。导入不发网络请求，来源可信度依赖浏览器采集证据，脚本仅验证声明、主机与文件格式。

`crawl.last_checked_at` 是最近检查时间。元数据更新可以改变此时间和页面字段，但不应把既有文件的 `downloaded_at` 改成元数据检查时间。

## 状态与版本

`crawl-state.sqlite3` 维护持久采集状态。成功缓存可在默认运行时复用；失败不应永久阻止下一次尝试。完整性检查失败的缓存需要重新获取。

- 相同 URL 且成功缓存可用：默认跳过文件网络下载；需要检测变化时使用 `--refresh`。
- 不同 URL、相同 SHA-256：共用同一内容文件，保留全部来源。
- 相同 URL、不同 SHA-256：保留不同内容版本，不能覆盖掉先前原件。
- 不同 SHA-256 但标题或编号相同：仍保存为不同内容，不据此合并或删除。

`--refresh-metadata` 只处理已有文件的来源详情元数据。它不能证明 PDF 内容未变化，也不能补出页面没有披露的信息。

## 多平台补充

- `document_type`：带来源的资料类别，本技能已接受的原件为 `regulation`；不将裁决、草案、摘要和 FAQ 混入。
- `effective_at`：个别平台提供的生效日期；无法确定格式时只保留 `raw` 并标 `unparsed`，不填入公布日期。
- `member_detail_url`：JDIHN 明确列出的成员详情页（若提供），保留聚合索引与原始成员的关系。
- `platform-status.json` 是跨运行的逐平台状态快照，各条有自己的检查时间；`last-run.json` 只表示本次运行。
- 元数据刷新若发现详情不再引用旧文件，或该 URL 已知对应另一个内容哈希，记录 `stale_source` 并保留旧文件元数据。仅链接匹配不能证明远端字节未变化，检查内容变化仍使用 `--refresh`。

## 多格式原件

`file.path` 不限 `.pdf`，`file.media_type` 按实际字节识别；JSON 继续使用 schema 2，无需迁移已存 PDF。二进制新文件名为原始 SHA-256 加真实扩展名；HTML/XML/JSON 法规文本经现代批次入口转换为 Markdown，另保留原始响应及审计元数据。支持范围与格式校验边界见 [file-formats.md](file-formats.md)。运行报告的 files 条目同时提供 `media_type`；不要按扩展名假定内容。

## 国家目录与加拿大日期

每份 sidecar 和国家状态库位于 `<downdloadDir>/<Country>/`；`file.path` 相对此国家目录。加拿大 jurisdiction 为 CA；`updated_at` 保留网页 dcterms.modified，`consolidated_to` 与 `last_amended_at` 单列，不能作为公布日。旧根目录文件不自动迁移。详见 [canada.md](canada.md)。

## 最新候选、批次和失败记录

现代入口在发现范围内按明确法规身份分组，比较 `published_at` 选择最新候选；`updated_at`、抓取时间、URL 排序不参与替代。日期未知、同日并列、身份不足保留候选和待确认原因，不能宣称全量最新。相同字节的全部来源仍在 `sources` 保存，`preferred_source` 记录主来源选择及未知日期提示。

每批次在配置基础目录 `.batches/<ID>/` 记录执行计划与候选快照；每次执行有独立尝试目录及 JSON、Markdown、HTML 报告。目录候选、每文件尝试与最终结果均记录状态/原因；默认最多 3 次包含首次。`metadata` 模式不请求原文附件，目录记录与成功文件元数据不可混称下载原文。详情见 [批次工作流](batch-workflow.md)。
