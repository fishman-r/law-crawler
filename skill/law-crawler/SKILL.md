---
name: law-crawler
description: >-
  This skill collects official regulations and registered judicial guidance into a local, jurisdiction-specific library. It applies to 法规下载、司法解释、全量采集、元数据、官方原文、批次重跑、Markdown转换、原始归档、本地OCR、低成本翻译与独立复核, official legislation downloads, judicial circulars, bulk archival, and offline repair from registered country or jurisdiction indexes. It does not collect individual court judgments, determine legal validity, or cover unregistered jurisdictions.
metadata:
  agent_created: "true"
---

# 采集官方法规到本地

在Qoder、Claude Code、DeepSeek Harness、Codex等工具接入时，按[跨工具兼容](references/agent-compatibility.md)连接同一份源码；使用当前工具实际提供的文件、终端与浏览器能力。

## 确认输入与运行位置

1. 获取用户明确提供的**适用国家/法域**。缺少时先询问，不从域名、安装位置或历史默认值猜测。重跑批次时读取原计划中的已确认法域及模式。
2. 获取采集模式。除非用户已明确要求法规原文，否则先询问“是否先只下载元数据（目录）？”，等待选择`metadata`或`content`。沿用同一任务已有明确选择，不重复询问；非交互任务必须显式提供模式。
3. 将本文件解析目录链接后的真实所在目录记为`SKILL_DIR`，将用户选择的工作区绝对路径记为`WORK_ROOT`；不要把技能安装目录当下载工作区。选择完整的Python 3.10+解释器（含`ssl`、`sqlite3`），按[工作区准备](references/workspace.md)完成只读检查或首次初始化。
4. 使用工作区自有的`WORK_ROOT/countries/`；源码工程根没有该目录时，读取工程内`skill/law-crawler/countries/`中的索引和默认配置，其默认采集输出集中到工程`data/downloads/`。独立包和显式用户配置仍按指定工作区解析。普通新工作区须按准备流程初始化，不自动借用安装包作为工作区。校验实际选中的`countries/*/index.md`登记。未知法域在网络请求和采集批次创建前停止；已有索引未登记的法域不得通过模板补入。只在工作区尚无索引且用户已明确法域时，从`SKILL_DIR/countries/`初始化。区分“已登记”“已适配”和“网络已验证”。

## 选择操作并执行

将下列路径占位符替换为实际绝对路径；将`python`替换为已检查的解释器。使用单行命令，避免依赖特定shell的变量或续行语法。

```text
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction Canada --mode metadata --max-details 4
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --rerun-batch BATCH_ID
```

- 执行“原始归档→低成本处理→高质量复核”时，读取[三阶段处理](references/staged-pipeline.md)。先冻结已确定的来源清单，再归档所有响应；使用本地模型生成中文、英文派生稿，按原件与产物哈希绑定独立复核。保留原文，不将下载成功或模型自评等同于复核通过。 复核失败时按同一参考文档建立新的修订批次，对扫描/混合PDF使用全页哈希绑定的转录入口，以原件页证据补足日期与图表关系，并由不同复核者重新验收；不得覆盖旧稿或自行改为通过。
- 执行本地自动OCR或扫描表格处理时，读取[本地OCR与表格](references/local-ocr.md)。先归档原PDF，再选择实际可用的本地引擎；保留文本框与页图，不把高置信度或识对全部数字等同于文字及行列关系正确。
- 翻译法规时读取[法律翻译文体](references/legal-translation.md)，采用已确认的目标法域官方法律表达基准，避免逐词直译；将文体配置冻结进新批次，并独立复核语义和文体。默认中文中国大陆、英文英国，仅调整表达，不移植实体法律规则。
- 执行传统目录/原文采集或批次重跑时，读取[批次工作流](references/batch-workflow.md)，让脚本先冻结计划再请求网络。根据已确认模式传参；示例中的Canada不是默认法域。
- 用户要求遍历目录、统计全量或核对覆盖率时，读取[目录普查与计数](references/catalog-census.md)，冻结截止日和版本范围，仅采目录；将未知分母与零条结果区分。
- 用户要求平台能力或覆盖率矩阵时，使用[覆盖率能力矩阵](references/coverage-matrix.md)，将源码支持、已有运行证据、队列完成率和法规覆盖率分开；没有同范围分母时保留未知，并列出逐平台缺口。
- 执行全量或大批量归档时，读取[批量归档与覆盖验收](references/bulk-collection.md)。遍历已适配的真实分页并保存断点；先归档全部已发现版本，再导出处理队列。将未适配分页、来源受阻、未确认目录总量及待独立复核分别列入缺口。印尼司法规范保留PERMA、SEMA原始文种，不将其法律地位等同于中国司法解释。
- 用户明确只要原始下载时，停在原件、元数据和下载报告，不自动接入OCR、翻译或内容复核。已有冻结目录的批次可按[纯下载续跑](references/bulk-collection.md#纯下载续跑)使用默认2路、最多5路并发；保留原计划签名、逐文件失败状态与收据，不将请求并发等同于全国采集完成。
- 修复“未发现原件”时按[原件缺口修复](references/bulk-collection.md#修复未发现原件)冻结目标分区，区分解析遗漏与官方未提供全文；跨来源补收须绑定完整法规身份与证据哈希，保留原站缺件事实，不能把封面、译文或访问错误页计作正文齐全。
- 需要跨 agent 重放筛选、审核合批和 OSS 验收时，使用[可重放修复流程](references/recovery-workflow.md)的工程内脚本；显式传入冻结清单、审核文件及独立输出路径，不依赖旧会话临时脚本。
- 继续处理待核身份或附件时，使用[待核裁定](references/pending-adjudication.md)区分候选错配、OCR错误、目录错误及原件内部异常；绑定旧审核与新证据，明确缺附件时只接收正文并保留不完整状态。
- 为已有 `incomplete` 任务追加附件时，使用[附件续收闭环](references/attachment-recovery.md)，保留原正文与已收附件；将每个已知缺件绑定到独立批准的新附件，先冻结、再执行、最后实际回读审计。只补齐部分缺件时继续保持不完整，不把解决已知缺件等同于全版本或全部附件覆盖。
- 处理具体来源时，仅加载对应说明：[印尼平台](references/platforms.md)与[印尼核验](references/indonesia.md)、[加拿大](references/canada.md)、[EU及成员国](references/eu.md)。从工作区索引取得实际范围；索引导航、裁决或未适配来源不算法规下载成功。
- 处理文件字段、正文转换或离线Markdown重建时，读取[元数据](references/schema.md)与[文件格式](references/file-formats.md)。字段标注显示名称和值，不追加`(XML 路径)`括号；保留可读层级及未知语义，不摊平为无名数字。
- 处理TLS、403或正常浏览器补收时，分别读取[网络排查](references/network-troubleshooting.md)与[浏览器导入](references/browser-workflow.md)。使用当前agent已提供的工具及其文档；没有浏览器工具时报告限制。不得照抄历史工具句柄、固定本地路径或把旧网站状态当作当前事实。
- 维护 HTTP 下载或新增平台请求时，读取[统一下载内核](references/download-kernel.md)，复用有界流式传输和单层重试；保留服务端冷却时间，不在适配器中另套重试。
- 配置全量归档、OSS 或目录截点时，读取[显式运行配置](references/runtime-configuration.md)：新批次明确法域与存储目标，从冻结目录取得范围和实际总量；续跑沿用旧计划并拒绝冲突，不改写历史计划。

## 保留采集约束并交付

- 按配置`downdloadDir`（兼容`downloadDir`）保存到国家子目录；为每份下载内容关联元数据，并分别记录原始与派生文件的实际哈希。保留二进制原格式；仅将确认是法规正文的HTML/XML/JSON转成Markdown。
- 按有证据的法规身份和最新公布日期选择版本；缺少`published_at`或同日并列时保留待确认，不以抓取时间或网页更新时间代替。沿用URL、队列、持久状态和原字节SHA去重，保留历史原件及全部来源。
- 使用默认最多3次总尝试的有界退避；逐文件记录成功/失败、尝试次数和原因。将缓存复用、原文请求、正文身份核验和法律效力分开报告；缺证据字段保持`null`。
- 交付法域、模式、批次ID、实际目录、成功/复用/重复/失败/待确认数量及Markdown、HTML报告。说明采样边界、未适配平台和未解决问题；不得仅凭退出码或文字存在认定完成。
