# 加拿大验证流程

> 按本文流程处理该法域；将带日期、“本轮/本机”及库存数字的段落视为2026年10月历史观察，重新核实当前响应。工程reports路径仅供维护者追溯，独立包不包含且不依赖这些历史文件。

读取工作区 `countries/Canada/index.md`，遵循其免 API Key、非 PDF 优先偏好。索引包含法规、判决及学术数据三类来源，不是所有链接都属于可下载的官方法规正文。

以下原文命令仅用于用户已明确选择 `content` 的任务；未明确原文时先询问是否仅采集目录元数据，选择后使用 `--mode metadata`。

## 可执行的法规链路

- `open.canada.ca`：从工作区列明的联邦 XML 数据集页面取得司法部 `Legis.xml` 目录链接，读取英文条目的官方详情，再下载该详情明确提供的 XML 原件。目录索引不是法规原文；元数据模式保存其发现的法规候选元数据。
- `laws.justice.gc.ca`：从 Acts / Regulations 目录交替选取有限样本，读取正式法规目录页的 XML 全文链接。允许司法部同一站点的 `laws-lois.justice.gc.ca` 域名。
- 单部 XML 只接受加拿大司法部 `Statute` / `Regulation` 白名单结构：LIMS 身份、标题、编号及正文条文缺一不可。普通 XML、目录、错误页不能作为原件。

```text
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction Canada --mode content --index countries/Canada/index.md --platform all --limit 6 --per-platform-limit 2 --max-details 4
```

结果保存到 `<downdloadDir>/Canada/`。此命令按主机列报告，包含尚未适配或非本技能范围的来源；有原件时退出 0 不代表所有索引平台都完成采集。固定验证数据集地址只是目录入口，不是用固定文件替代动态发现。当前只取英文有限样本，不做全量或全语言覆盖。

## 元数据含义

继续使用 schema 2。`legal_identity.jurisdiction=CA` 表示文献归属，不代表已核实全国适用。保留官方标题及引注；年、公布日或适用地区缺少证据时为 null。

- `updated_at`：详情页的 `dcterms.modified`，明确为页面声明修改日期。
- `consolidated_to`：详情页 `current to`，合并文本截至日期。
- `last_amended_at`：详情页 `last amended on`，最近修订日期。
- `text_version=consolidated`：合并文本，不能称为原始颁布版。
- 不把这些日期填成 `published_at`；XML 自身的 LIMS 日期可能与页面 current-to 不同，原 XML 在 `raw/` 原样保留用于审计，正文另存 Markdown；各字段保留其来源，不能混用。

司法部对日期及 XML 下载的说明：[官方 FAQ](https://laws.justice.gc.ca/eng/FAQ/)。本技能不作法律效力判断，`legal_status=not_verified`。

## 索引其他来源

SCC/Lexum、Federal Court 是法院判决，A2AJ 是学术开放数据；它们可做目录可访问性核查，但不混入法规存储。GitHub 链接是司法部 XML 仓库的说明引用，不能把 `github.com` 整个主机当成通用政府法规平台。法院正文或学术语料的实际下载需要独立明确的采集类型和来源规范。

2026-10-14，司法部两条发现链取得 3 份不同 XML：Access to Information Act、Canada Occupational Health and Safety Regulations、“MV Sonia” Remission Order, 2007。开放门户和司法部站点发现的相同 A-1 字节合并并保留两条来源。法院及学术来源本轮只核查访问，不宣称正文采集成功。工程证据为 `reports/canada-verification-2026-10-14.json` 与 `reports/canada-source-probe-2026-10-14.json`。


## 优化后加拿大复验中的版本与别名处理

司法部目录的 XML 链接是官方当前合并文本。`published_at` 未提供时仍为 null，`consolidated_to`、`last_amended_at`、XML 的 LIMS 日期分别保存，不替代法规公布日期。

司法部两个已确认官方主机 `laws.justice.gc.ca` 与 `laws-lois.justice.gc.ca` 的同路径正文使用 `official_resource_uri` 标识同一资源，实际请求链接仍保留原样。只有法规身份、资源标识和官方版本观察一致时，重复入口选一个下载代表，标记 `same_official_resource_date_unknown`；另一个标记 `duplicate_official_resource`，全部目录证据保留。不同正文或版本观察冲突且公布日期未知时，仍不猜测最新版。

已转换的相同原始字节复用首次转换来源和结构证据，避免两个官方别名令 Markdown 溯源头部变化而产生虚假的新内容哈希。需要确认远端当前正文时使用 `--refresh`；按批次默认重跑复用已验证成功项，`--rerun-all` 沿用原计划中的刷新选项。

2026-10-15复验发现并修复默认重跑的限量问题：前轮成功缓存未计额度会让原本达到平台限量而跳过的候选继续下载。执行器现在先为全部有效成功项预留额度，避免较早的待执行项抢占较晚成功项的额度；原件/Markdown损坏则不虚假复用。历史超额文件保留，本轮按原计划裁剪，不通过删除历史或改写旧批次掩盖问题。验证时须分别报告首轮、问题轮、修复后默认重跑和全项刷新，以及实际国家目录库存；最终证据见工程 `reports/canada-optimized-validation-2026-10-15.md` / `.html`。

同日用户提出结构问题后，全库7份加拿大Markdown复核均不合格：v1把日期年月日、条款编号及表格/脚注结构摊平；此前文本片段都能检出并不代表结构完整。`structured_text_v2.2`具名保留RegistrationDate、ConsolidationDate和制令日期，各日期不混入公布日或详情current-to，深层条款通过完整路径保留父子语境；原XML属性和未生效/关联源栏目按原语境保留，公式保存源结构，脚注没有idref时明确未解析。使用`rebuild_markdown.py`离线升级现有7份、保留原XML/来源/请求计数及旧输出备份，后续缓存复用也升级转换版本。报告 `reports/markdown-structure-repair-2026-10-15.md` / `.html` 明确结构检查与既有文本存在检查的区别。

同轮独立审查还发现同一Markdown字节跨转换版本时的历史元数据文件名碰撞，已改为按完整元数据快照SHA独立备份。此前3份v2.1历史快照没有完整保留，已通过`canada-conversion-history-audit-20261015`登记`mismatch/snapshot_unavailable`，不补造时间等历史值；当前正文、原XML和当前元数据完好。交付须把当前结构验收与这3个历史证据缺口分开报告。
