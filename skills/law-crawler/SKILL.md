---
name: law-crawler
description: >-
  This skill collects official regulation metadata and original legal documents into a local, jurisdiction-specific library. It applies to requests for 法规下载、法规目录、元数据采集、官方原文、批次重跑、Markdown转换, official legislation downloads, regulation catalogs, batch replay, and offline Markdown repair from registered country or jurisdiction indexes. It does not collect court judgments, determine legal validity, or cover unregistered jurisdictions.
metadata:
  agent_created: "true"
---

# 采集官方法规到本地

## 确认输入与运行位置

1. 获取用户明确提供的**适用国家/法域**。缺少时先询问，不从域名、安装位置或历史默认值猜测。重跑批次时读取原计划中的已确认法域及模式。
2. 获取采集模式。除非用户已明确要求法规原文，否则先询问“是否先只下载元数据（目录）？”，等待选择`metadata`或`content`。沿用同一任务已有明确选择，不重复询问；非交互任务必须显式提供模式。
3. 将本文件所在目录记为`SKILL_DIR`，将用户选择的工作区绝对路径记为`WORK_ROOT`；不要把技能安装目录当下载工作区。选择完整的Python 3.10+解释器（含`ssl`、`sqlite3`），按[工作区准备](references/workspace.md)完成只读检查或首次初始化。
4. 使用工作区自有的`WORK_ROOT/countries/`；源码工程根没有该目录时，读取工程内`skills/law-crawler/countries/`中的索引和默认配置。普通新工作区须按准备流程初始化，不自动借用安装包作为工作区。校验实际选中的`countries/*/index.md`登记。未知法域在网络请求和采集批次创建前停止；已有索引未登记的法域不得通过模板补入。只在工作区尚无索引且用户已明确法域时，从`SKILL_DIR/countries/`初始化。区分“已登记”“已适配”和“网络已验证”。

## 选择操作并执行

将下列路径占位符替换为实际绝对路径；将`python`替换为已检查的解释器。使用单行命令，避免依赖特定shell的变量或续行语法。

```text
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --jurisdiction Canada --mode metadata --max-details 4
python -X utf8 "SKILL_DIR/scripts/download_regulations.py" --root "WORK_ROOT" --rerun-batch BATCH_ID
```

- 执行新采集或批次重跑时，读取[批次工作流](references/batch-workflow.md)，让脚本先冻结计划再请求网络。根据已确认模式传参；示例中的Canada不是默认法域。
- 处理具体来源时，仅加载对应说明：[印尼平台](references/platforms.md)与[印尼核验](references/indonesia.md)、[加拿大](references/canada.md)、[EU及成员国](references/eu.md)。从工作区索引取得实际范围；索引导航、裁决或未适配来源不算法规下载成功。
- 处理文件字段、正文转换或离线Markdown重建时，读取[元数据](references/schema.md)与[文件格式](references/file-formats.md)。字段标注显示名称和值，不追加`(XML 路径)`括号；保留可读层级及未知语义，不摊平为无名数字。
- 处理TLS、403或正常浏览器补收时，分别读取[网络排查](references/network-troubleshooting.md)与[浏览器导入](references/browser-workflow.md)。使用当前agent已提供的工具及其文档；没有浏览器工具时报告限制。不得照抄历史工具句柄、固定本地路径或把旧网站状态当作当前事实。

## 保留采集约束并交付

- 按配置`downdloadDir`（兼容`downloadDir`）保存到国家子目录；为每份下载内容关联元数据，并分别记录原始与派生文件的实际哈希。保留二进制原格式；仅将确认是法规正文的HTML/XML/JSON转成Markdown。
- 按有证据的法规身份和最新公布日期选择版本；缺少`published_at`或同日并列时保留待确认，不以抓取时间或网页更新时间代替。沿用URL、队列、持久状态和原字节SHA去重，保留历史原件及全部来源。
- 使用默认最多3次总尝试的有界退避；逐文件记录成功/失败、尝试次数和原因。将缓存复用、原文请求、正文身份核验和法律效力分开报告；缺证据字段保持`null`。
- 交付法域、模式、批次ID、实际目录、成功/复用/重复/失败/待确认数量及Markdown、HTML报告。说明采样边界、未适配平台和未解决问题；不得仅凭退出码或文字存在认定完成。
