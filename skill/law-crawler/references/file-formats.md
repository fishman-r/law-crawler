# 文件格式与法规文本转换

现代批次入口先确认官方法规来源，再用共享 `document_formats.py` / `CrawlStore` 校验二进制原件，用 `text_documents.py` 校验并转换文本原件。HTTP MIME 或文件后缀不能代替格式与正文确认。历史低层存储入口可保留原 XML；现代原文模式显式启用文本转换。

## 支持范围

| 官方原格式 | 主保存格式 | 检查范围 |
| --- | --- | --- |
| 加拿大司法部 XML | `.md`，原 XML 审计副本 | 白名单 Statute/Regulation 身份、标题、条文结构；目录和错误 XML 拒绝。 |
| 已确认法规正文 HTML | `.md`，原 HTML 审计副本 | 必须明确 `#id` 或唯一 `article/main` 正文容器，排除错误/挑战标题，剔除导航和脚本。 |
| 已确认法规正文 XML | `.md`，原 XML 审计副本 | 适配器声明真实正文根节点；拒绝 DTD/实体、错误/目录根节点、空正文。 |
| 已确认法规正文 JSON | `.md`，原 JSON 审计副本 | 适配器声明顶级正文键，需法条文字，拒绝错误/挑战响应。 |
| PDF | `.pdf` | 文件头和结束标记检查，不保证所有对象可解析。 |
| OOXML | `.docx` / `.xlsx` / `.pptx` | ZIP/CRC、内容类型、主文档关系及主 XML；不渲染。 |
| OpenDocument | `.odt` / `.ods` / `.odp` | mimetype、主 XML、manifest 根节点及 ZIP 校验。 |
| RTF | `.rtf` | 签名、大括号结构；二进制段暂不支持。 |
| PNG | `.png` | 分块、CRC、压缩流、像素行长度；隔行编码暂不支持。 |
| JPEG | `.jpg` | 帧/扫描分量、扫描流、结束标记；不是完整图像解码。 |
| ZIP 原件包 | `.zip` | 目录、CRC、条数、展开总量检查；整体原样保存，不展开到磁盘。 |

暂不接受旧 OLE Office、TIFF、宏 OOXML、加密 ZIP、独立 TXT/CSV、未知二进制。HTML/XML/JSON 只有具备法规正文识别证据时才接受；不能仅将 `document_type` 写为 regulation 就把任意网页或接口响应放行。

通用 HTML/XML/JSON 的元数据必须包含带来源的 `text_body_confirmed.value: true`，HTML 还需 `text_body_selector`，XML/JSON 还需 `text_body_root`。加拿大 LIMS XML 沿用已验证的白名单结构。转换器接收有效 UTF 编码，拒绝空字符、不支持控制字符、过深嵌套和超过 40 MiB 的文本；不猜测编码或替换丢失字符。

ZIP 上限为 10,000 项、展开总量 200 MiB、被解析 XML 8 MiB。大小限制和格式结构校验不是恶意代码扫描；不执行脚本、宏或嵌入对象。正文转换不是 OCR、翻译或法律效力判断。

## 文件与元数据对应

- 二进制原件：`<原始sha256>.<真实扩展名>` 与 `<原始sha256>.json`。
- 文本原始响应：`raw/<原始sha256>.<真实扩展名>` 与 `raw/<原始sha256>.<真实扩展名>.metadata.json`。
- Markdown 主文件：`<原始sha256>.md` 与 `<原始sha256>.json`。文件名以原始哈希标识原文身份；元数据 `file.sha256` 是 Markdown 实际字节的哈希，不能由文件名推断。
- 主元数据 `original_file` 描述原响应，`file` 描述主 Markdown，分别记录路径、格式、字节数及 SHA-256；`conversion.metadata` 固定保留当次正文结构识别证据，缓存校验不受后续来源汇总字段变化影响。

转换保留原字节及来源审计副本，不把原 XML/JSON 直接改后缀为 Markdown。输出按结构保留标题、段落、表格、列表及链接，活动 HTML 转义，不执行远端内容。每份实际下载内容及派生主文件均有元数据关联。

## 结构验收与已有Markdown升级

`structured_text_v2.4`区分原文字节完整性、文字保留和结构保留。2026-10-15实查全部7份加拿大Markdown均存在结构语义缺失；此前68,654个文本片段存在检查未发现缺字，仍不能证明转换合格。不要以“转换成功”“所有文本都找到”替代以下核对：

- **字段与日期**：XML叶字段保留名称、上下文和必要源属性，字段后不追加`(XML /… )`路径括号；原路径仍在原XML中，必要条款/属性源注记单独保留。不能把YYYY/MM/DD摊平成无名数字。加拿大`RegistrationDate`标为登记日期、`ConsolidationDate`标为XML合并日期，制令日期标为命令日期；它们不回填`published_at`或详情页`current-to`。LIMS日期按来源原名展示，不推断效力。JSON键和数组路径保留字段语境。HTML的`dl/dt/dd`保留具名术语/定义及起止边界，多术语/多定义按源顺序保留，不猜一对一字段；`sup/sub`明确标为上下标，`time`的datetime只标源属性，图片仅记录alt/src。
- **条款与引用**：具名Section/Subsection/Paragraph等保留层级和编号，超过Markdown六级标题的条款通过完整XML路径补足父子语境；标签与文字不黏连。脚注依真实idref配对，缺失/歧义明确标待确认，不猜目标。交叉引用和图片保留源属性，图片引用不等于图像已下载或OCR。
- **表格与特殊结构**：CALS/HTML输出真正Markdown行列和表头，跨行/跨列用原坐标续格及跨度说明表达；不冒充原版式。MathML保留惰性源代码结构，不把线性文本当公式排版；`RelatedOrNotInForce`等源栏目保留起止边界，不自行判断其中条文效力。未知XML结构保留标签/路径/属性，完整原结构仍以raw为准。
- **双语节点**：`BilingualItemEn`与`BilingualItemFr`在表格单元格、正文内联及独立节点中分别标为`[English: 原文]`、`[Français: 原文]`，逐节点保留语言边界和源顺序；不将两个语言正文黏连，不猜配对或翻译，不给标签追加XML路径括号。

固定原始SHA及首次转换的`conversion.metadata/source_url`，以新转换版本重建派生文件。`conversion.method`记录版本、`converted_at`记录本地生成时间，不改变原件实际下载时间或日期含义。旧输出及元数据保存在`conversion-history/<原始SHA>/<旧Markdown实际SHA>.md`及`<旧Markdown实际SHA>-metadata-<完整快照SHA>.json`（旧备份也可能使用无快照SHA的历史路径），`conversion_history`记录实际旧SHA与元数据期望SHA是否一致，并记录元数据备份自身SHA。相同正文不同转换版本也须分别保存完整元数据，不能仅按Markdown哈希复用元数据。已发现缺失/不匹配的历史快照须明确登记证据缺口，不猜测补造。只有派生Markdown改变哈希，原XML和原始SHA不变，URL去重/来源/请求计数不增加。正常缓存复用和默认批次重跑也会升级旧版转换，避免长期返回旧坏格式。

修改会影响输出的渲染规则时同步变更`CONVERSION_METHOD`，否则旧缓存无法识别需要升级。重建批次冻结目标转换版本；后续转换器再升级应新建批次，不能改写旧计划。源表未声明表头时，`Column n`仅为生成的位置标签，不是补造的法律字段名。多行源表头的所有行和thead路径保留，但Markdown语法只把第一行作为表头，其余为表格行；不宣称复现原始多级表头视觉分组。

对现有国家目录执行显式离线重建：

```text
python -X utf8 "SKILL_DIR/scripts/rebuild_markdown.py" --root "WORK_ROOT" --jurisdiction Canada --batch-id canada-markdown-rebuild-RUN_ID

python -X utf8 "SKILL_DIR/scripts/rebuild_markdown.py" --root "WORK_ROOT" --rerun-batch canada-markdown-rebuild-RUN_ID
```

该工具只处理已有且有原始响应的Markdown记录，不下载新法规。先冻结原件身份和转换证据，再逐文件校验、备份、重建并输出JSON/Markdown/HTML报告；重跑沿用原计划。同版本有效文件记`already_current`，不重复备份；可用`--force`显式重算同版本，原计划随之冻结。原始响应损坏或冻结来源变化时记录失败，不能补造来源/跳过校验；有失败时不通过全库清单更新副作用重建被拒绝项。缺失原件须另行明确原文采集任务。

当前实跑结构验收覆盖加拿大XML；通用HTML字段列表/上下标/日期属性/图片引用/表格、JSON路径及未知XML由离线夹具覆盖，不宣称已通过其他官网在线正文验收。检查新输出的日期角色、条款/表格/脚注和原文对照，不能只统计数值行或标题数量。

## 发现、去重与验收边界

`Document.file_urls` 是通用候选入口，旧 `pdf_urls` 保持兼容；后缀或无后缀路由只是候选筛选，需继续官方主机、资料类型与实际字节校验。图片仅接受官方明确提供的扫描原件，不采页面图标。

SHA-256 去重基于原始响应字节；相同原文跨来源合并，不同原文保留历史。不同格式即使含义相似也不自动合并。重建及库存核对应读取 sidecar 的 `file.path` 和 `original_file.path`，不能只扫描 PDF 或只看扩展名。

历史加拿大 XML、德国 XML ZIP 实跑证据见 [加拿大](canada.md)和 [EU](eu.md)。这些记录只证明旧流程在对应时间取得原件，不证明新 HTML/JSON 或 Markdown 转换已完成各站网络验收。离线转换测试不能替代官方目录→详情→正文的真实链路验证。
