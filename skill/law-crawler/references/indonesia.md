# 印尼验证与验收边界

执行用户明确要求的原始归档、本地双语处理与独立复核时，先读取[三阶段处理](staged-pipeline.md)。将下文两阶段命令用于来源发现和传统下载，不将原件下载成功等同于翻译质量通过。

处理扫描PDF或旧OCR隐藏文字层时，按[本地OCR与表格](local-ocr.md)执行全页识别和独立复核。核对地名及术语：Jawa为爪哇、Lampung为楠榜，`derajat sosoh`指碾白程度，不能译成脱壳率；区分`Pasal`（条）与`ayat`（款）。对价格表保留`Rp/kg`、Medium/Premium列身份及印尼千位分隔符。分开记录签发、公布和具体措施实施日期；核对废止条款的主管机关与适用限定，不把印章噪声当人名。57/M-DAG/PER/8/2017在官方目录标为`Tidak Berlaku`，仅作为历史表格格式样本，不据此回答现行大米价格。

> 按本文流程处理该法域；将带日期、“本轮/本机”及库存数字的段落视为2026年10月历史观察，重新核实当前响应。工程reports路径仅供维护者追溯，独立包不包含且不依赖这些历史文件。

使用工作区 `countries/Indonesia/index.md`。用户已明确原文要求时，先采元数据、后建独立原文批次：

```text
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction Indonesia --mode metadata --platform all --limit 22 --per-platform-limit 2 --max-details 2 --timeout 12 --delay 0.5 --experimental-sources

python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction Indonesia --mode content --platform all --limit 22 --per-platform-limit 2 --max-details 2 --timeout 12 --delay 0.5 --experimental-sources --refresh
```

示例是每平台两个详情的有界验证，不保证全量或全站最新。`--experimental-sources` 是已核实的 JDIHN 官方 dev 备用模式，须在计划和报告中明确；正式入口的失败诊断继续保留。

默认批次重跑会复用成功文件，重新尝试失败的目录/详情和原件；所以部分平台仍受阻时，默认重跑仍可能产生失败请求，但已经成功的正文不应重复请求。`--rerun-all` 沿用原计划的刷新开关，对全部选中原件重新验证字节；失败请求达到上限后结束本轮，保留错误状态及原因。

## 法规身份、日期和资料类型

正式法规与裁决、草案、FAQ、摘要分开。MK 使用 PMK 正式法规栏目，DPR 使用 PerDPR，MA 按正式类型分别采集 PERMA 与 SEMA；保留原始文种，不将其自动等同于中国司法解释。个案 Putusan、草案及行政内部文书不因同属司法机关就混入法规。

`published_at` 取官方 Pengundangan 日期，不能将网页修改时间或下载时间代填。MA/OJK 若无日期则标待确认。同身份多版本/多来源才比较公布日期；每个候选各为独立法规的样本不能当作在线新旧版本竞争验证。历史库存数量以本轮目录基线为准，旧报告中的数量不作为当前库存。

贸易部详情须有正式法规类型、标题及本法编号/年份。只按 `/peraturan/peraturan-*` 路径不足以核实类型。排除摘要、FAQ、草案附件及 `/frontend-new/` 全站前端应用资源 ZIP，不能把应用资源包当法规原件；法规 Lampiran 链接保留作后续附件核查，有限样本下载不保证附件齐全。

## PDF与Markdown

印尼当前适配主要提供官方 PDF。传统下载流程保留原始字节及独立JSON元数据，不执行OCR或PDF正文Markdown提取；用户要求三阶段处理时，按[三阶段说明](staged-pipeline.md)提取文字或导入逐页转录，再生成原文及双语Markdown。HTML详情、嵌入JSON目录字段只属于来源元数据，不能充当HTML/JSON法规全文。

下载成功后核对实际正文页的标题、法规类型、编号和年份，不能把封面当正文。扫描件提取不到文字时，先渲染关键页视觉核对；未能确认才记录未知，OCR全文另行处理。

2026-10-14 首轮仅凭提取文字把 PMK23/2022 记为草案冲突，后续复核纠正：`RANCANGAN` 在内容流中设置为白色（`1 g`），随后正式标题恢复黑色（`0 g`）；真实渲染首页、制定语句、公布及认证页支持正式身份，隐藏模板残留是综合证据的推断。不能单凭该词判草案，也不能仅凭签名忽略可见冲突。PERMA2/2015虽然前两页无提取文字，但扫描首页和末页可视觉核实身份与公布信息。两份原字节都不修改，首次审计保留；更正前的审核存入 `body_review_history`，新审核列明页码、图像及内容流证据。本次不判断法律效力或全站最新。

正文身份审查不强制安装 PDF 依赖。可使用所在环境的文本提取或页面查看能力，产出显式审核 JSON，然后用标准库导入器登记：

```text
python -X utf8 "SKILL_DIR/scripts/import_body_review.py" --root "WORK_ROOT" --country Indonesia --directory "COUNTRY_DIRECTORY" --audit "AUDIT_PATH"
```

将`country_directory`设为实际配置的国家目录，`audit_path`设为本次生成的审核JSON路径；不要依赖历史工程报告文件。导入器不提取文本、不访问网络，以实际原文件 SHA 绑定审核，保存 `body_review.status/reason/evidence/reviewed_at` 到 SQLite 与文件 JSON，重建当前原件清单。确认的 `catalogue_body_type_conflict` 保留历史但排除当前候选；未知仍显示未知。显式更正保留前次审核历史，重复同一审核不追加；更正为身份匹配后可恢复为当前候选。标题匹配不代表全文、附件、法律效力或全站最新已核验。下载报告的成功状态只描述传输，不能修改历史批次报告冒充当时已核验。

实际 PDF 公布页可补 `published_at/promulgated_at`，制定页单列 `enacted_at`；字段来源须指向原件并标 PDF 页码。同 SHA、同来源的目录重跑未提供日期时，保留已观察到的 PDF 日期；新来源和新字节不能借用旧证据。MK PMK1/2026、PMK7/2025 本次只观察到制定日期，公布日期保持未知。

## 三语复核中的法规用语

按原文职权主体及法规层级逐项核对译文：区分 `Peraturan Pemerintah`（政府条例）、`Peraturan Presiden`（总统条例）及 `Peraturan Pemerintah Pengganti Undang-Undang`（代法律政府条例），不因同有编号就互换。将 `Cipta Kerja` 按创造就业理解；在林产品语境中将 `Provisi Sumber Daya Hutan` 作为森林资源征费核对，不能直接译为准备金或一般“provision”。

保留斜杠法规编号、带字母条号、废止对象与既有修订链。区分制定日生效与公布日生效；将 `dicabut dan dinyatakan tidak berlaku` 的废止、不再有效含义与自始无效区分。区分 `Lembaran Negara`、`Tambahan Lembaran Negara`、`Berita Negara` 的公报编号与法规编号。按原页当时名称翻译机构，不自动补入另一个部门或历史名称。

遇到QR、签章和验证页脚时，将可读内容或视觉说明与法规正文分开；不能可靠辨认的内容保留未知，不推断签名真实性。日期或公报号出现乱码时换独立PDF渲染器核对。含组织图时逐边检查上下级关系，不只核对节点数量；含表格时逐单元格检查行列归属与脚注（组织图样本通过不代表表格已验证）。

## 大原件与实际取得方式

MK 正常浏览器目录本次可读，公开 `s.mkri.id` 原件CLI可取，但CLI目录仍403。PMK1/2026完整74,168,413字节，PMK7/2025完整119,790,543字节，均超过40MiB默认上限。浏览器大PDF调用超时可能停在预览，拿不到本地路径时不能报浏览器下载成功。下载阶段须保留有限超时、最多3次重试与逐次事件；明确提高大小上限后再完整验证，截断响应拒绝入库。

若原件实际由CLI公开请求取得，复用文件导入器时显式标取得方式，不能伪造浏览器来源：

```text
python -X utf8 "SKILL_DIR/scripts/import_browser_download.py" --root "WORK_ROOT" --jurisdiction Indonesia --mode content --manifest "MANIFEST_PATH" --acquisition-method official_cli_download --max-mib 128 --batch-id "BATCH_ID"
```

该导入器不发网络请求，沿用国家配置、白名单、schema2、内容去重及批次重跑，报告模式为 `official_file_import`；浏览器实际取得的文件默认方式仍为 `browser_download`。重跑从原计划继承取得方式、上限及manifest快照，不能改方式。公开目录浏览器辅助加CLI文件补收不能算CLI自动目录链路恢复；验证结果见 `data/reports/indonesia-recovery-followup-2026-10-14.md` / `.html`。

传统下载器仅对实际取得且确认正文的HTML/XML/JSON触发Markdown转换；全部是PDF时说明该下载器未提取正文。三阶段流水线另行提取PDF或导入逐页转录时，分别报告原件下载、全文覆盖、双语转换和独立复核结果，不再以PDF格式本身判断Markdown不适用。

## 报告

每次尝试输出JSON/Markdown/HTML，包括成功、失败、跳过和中止。原文轮有部分失败时退出码0只说明有可用结果。逐项核对实际文件哈希、媒体类型、元数据、请求事件及两种可读报告；保留正式站/备用站、CLI/浏览器辅助的不同结论。

正文请求次数统计 `request_events`，不要对所有含 `url` 的状态直接求和：目录失败状态也有 URL 和次数，但不是正文请求。本轮原文完成轮 17 个正文请求事件，默认重跑 2 个失败正文请求，刷新重跑 16 个正文请求；首次单文件最多 2 次，均未超过默认上限 3 次。

本轮证据见工程 `data/reports/indonesia-optimized-validation-2026-10-14.md` 与 `.html`。两阶段、重跑和全量刷新使用同国家独立存储；本说明不进行法律效力核验。

## 全量任务的来源覆盖

先读取 `countries/Indonesia/index.md` 的“批量采集入口与完整性边界”。按已声明的来源和文种逐页遍历，并将每页 URL、响应证据、发现详情、重复详情、失败及待续 URL 存入可恢复批次。普通 `discover` 的首屏结果及提高 `--limit` 均不能证明全量。分开记录 PERMA 与 SEMA，严格校验详情中的 `Jenis`，并排除 `File Abstrak` 摘要下载。

核对目录日期的含义：MA 目录展示可能是制定日，例如 PERMA 3/2026 列表展示 2026-06-23，详情公布日为 2026-07-09；不能用列表日期代填 `published_at`。SEMA 详情若将制定和公布日期列为同日，仍保留官方标签与来源，待原件复核。

对 BPK 中央、部委／机构、地方、BPK 自身分类分别核对，对跨来源同身份去重前保留全部来源记录。不要将法规修订文件与被修订法规视为同一个身份；发布时间只用于同一身份和版本的候选排序，不能抹除修订链和已废止历史原件。

2026-10-21 观察到 JDIHN 正式站首页已提供 `/doc/数字` 和 `/search?c=all`；重新核对正式站路由，不能用旧 `/pencarian` 未发现详情证明全国无数据。页面访问、CLI 访问、原文传输分别记状态；浏览工具可见不代表下载器已恢复。全国规模的目录、归档、处理及复核分别计数；将无法枚举的来源、分页中断、未确认原件与尚未复核译文明确留在报告中。
