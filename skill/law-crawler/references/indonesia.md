# 印尼两阶段验证与验收边界

> 按本文流程处理该法域；将带日期、“本轮/本机”及库存数字的段落视为2026年10月历史观察，重新核实当前响应。工程reports路径仅供维护者追溯，独立包不包含且不依赖这些历史文件。

使用工作区 `countries/Indonesia/index.md`。用户已明确原文要求时，先采元数据、后建独立原文批次：

```text
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction Indonesia --mode metadata --platform all --limit 22 --per-platform-limit 2 --max-details 2 --timeout 12 --delay 0.5 --experimental-sources

python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction Indonesia --mode content --platform all --limit 22 --per-platform-limit 2 --max-details 2 --timeout 12 --delay 0.5 --experimental-sources --refresh
```

示例是每平台两个详情的有界验证，不保证全量或全站最新。`--experimental-sources` 是已核实的 JDIHN 官方 dev 备用模式，须在计划和报告中明确；正式入口的失败诊断继续保留。

默认批次重跑会复用成功文件，重新尝试失败的目录/详情和原件；所以部分平台仍受阻时，默认重跑仍可能产生失败请求，但已经成功的正文不应重复请求。`--rerun-all` 沿用原计划的刷新开关，对全部选中原件重新验证字节；失败请求达到上限后结束本轮，保留错误状态及原因。

## 法规身份、日期和资料类型

正式法规与裁决、草案、FAQ、摘要分开。MK 使用 PMK 正式法规栏目，DPR 使用 PerDPR，MA 使用 PERMA，不能因索引也包含裁决或草案就把该类资料作为正文采集。

`published_at` 取官方 Pengundangan 日期，不能将网页修改时间或下载时间代填。MA/OJK 若无日期则标待确认。同身份多版本/多来源才比较公布日期；每个候选各为独立法规的样本不能当作在线新旧版本竞争验证。历史库存数量以本轮目录基线为准，旧报告中的数量不作为当前库存。

贸易部详情须有正式法规类型、标题及本法编号/年份。只按 `/peraturan/peraturan-*` 路径不足以核实类型。排除摘要、FAQ、草案附件及 `/frontend-new/` 全站前端应用资源 ZIP，不能把应用资源包当法规原件；法规 Lampiran 链接保留作后续附件核查，有限样本下载不保证附件齐全。

## PDF与Markdown

印尼当前适配主要提供官方 PDF。PDF保留原始字节及独立JSON元数据，不执行OCR或PDF正文Markdown提取。HTML详情、嵌入JSON目录字段只属于来源元数据，不能充当HTML/JSON法规全文。

下载成功后核对实际正文页的标题、法规类型、编号和年份，不能把封面当正文。扫描件提取不到文字时，先渲染关键页视觉核对；未能确认才记录未知，OCR全文另行处理。

2026-10-14 首轮仅凭提取文字把 PMK23/2022 记为草案冲突，后续复核纠正：`RANCANGAN` 在内容流中设置为白色（`1 g`），随后正式标题恢复黑色（`0 g`）；真实渲染首页、制定语句、公布及认证页支持正式身份，隐藏模板残留是综合证据的推断。不能单凭该词判草案，也不能仅凭签名忽略可见冲突。PERMA2/2015虽然前两页无提取文字，但扫描首页和末页可视觉核实身份与公布信息。两份原字节都不修改，首次审计保留；更正前的审核存入 `body_review_history`，新审核列明页码、图像及内容流证据。本次不判断法律效力或全站最新。

正文身份审查不强制安装 PDF 依赖。可使用所在环境的文本提取或页面查看能力，产出显式审核 JSON，然后用标准库导入器登记：

```text
python -X utf8 "SKILL_DIR/scripts/import_body_review.py" --root "WORK_ROOT" --country Indonesia --directory "COUNTRY_DIRECTORY" --audit "AUDIT_PATH"
```

将`country_directory`设为实际配置的国家目录，`audit_path`设为本次生成的审核JSON路径；不要依赖历史工程报告文件。导入器不提取文本、不访问网络，以实际原文件 SHA 绑定审核，保存 `body_review.status/reason/evidence/reviewed_at` 到 SQLite 与文件 JSON，重建当前原件清单。确认的 `catalogue_body_type_conflict` 保留历史但排除当前候选；未知仍显示未知。显式更正保留前次审核历史，重复同一审核不追加；更正为身份匹配后可恢复为当前候选。标题匹配不代表全文、附件、法律效力或全站最新已核验。下载报告的成功状态只描述传输，不能修改历史批次报告冒充当时已核验。

实际 PDF 公布页可补 `published_at/promulgated_at`，制定页单列 `enacted_at`；字段来源须指向原件并标 PDF 页码。同 SHA、同来源的目录重跑未提供日期时，保留已观察到的 PDF 日期；新来源和新字节不能借用旧证据。MK PMK1/2026、PMK7/2025 本次只观察到制定日期，公布日期保持未知。

## 大原件与实际取得方式

MK 正常浏览器目录本次可读，公开 `s.mkri.id` 原件CLI可取，但CLI目录仍403。PMK1/2026完整74,168,413字节，PMK7/2025完整119,790,543字节，均超过40MiB默认上限。浏览器大PDF调用超时可能停在预览，拿不到本地路径时不能报浏览器下载成功。下载阶段须保留有限超时、最多3次重试与逐次事件；明确提高大小上限后再完整验证，截断响应拒绝入库。

若原件实际由CLI公开请求取得，复用文件导入器时显式标取得方式，不能伪造浏览器来源：

```text
python -X utf8 "SKILL_DIR/scripts/import_browser_download.py" --root "WORK_ROOT" --jurisdiction Indonesia --mode content --manifest "MANIFEST_PATH" --acquisition-method official_cli_download --max-mib 128 --batch-id "BATCH_ID"
```

该导入器不发网络请求，沿用国家配置、白名单、schema2、内容去重及批次重跑，报告模式为 `official_file_import`；浏览器实际取得的文件默认方式仍为 `browser_download`。重跑从原计划继承取得方式、上限及manifest快照，不能改方式。公开目录浏览器辅助加CLI文件补收不能算CLI自动目录链路恢复；验证结果见 `data/reports/indonesia-recovery-followup-2026-10-14.md` / `.html`。

仅实际取得已确认正文的HTML/XML/JSON才触发Markdown转换。若当轮实际全部是PDF，应报告“正文Markdown转换在线不适用”，并区分转换器离线回归已通过、Markdown格式执行报告已生成这两项；不能宣称印尼在线正文已转Markdown。

## 报告

每次尝试输出JSON/Markdown/HTML，包括成功、失败、跳过和中止。原文轮有部分失败时退出码0只说明有可用结果。逐项核对实际文件哈希、媒体类型、元数据、请求事件及两种可读报告；保留正式站/备用站、CLI/浏览器辅助的不同结论。

正文请求次数统计 `request_events`，不要对所有含 `url` 的状态直接求和：目录失败状态也有 URL 和次数，但不是正文请求。本轮原文完成轮 17 个正文请求事件，默认重跑 2 个失败正文请求，刷新重跑 16 个正文请求；首次单文件最多 2 次，均未超过默认上限 3 次。

本轮证据见工程 `data/reports/indonesia-optimized-validation-2026-10-14.md` 与 `.html`。两阶段、重跑和全量刷新使用同国家独立存储；本说明不进行法律效力核验。
