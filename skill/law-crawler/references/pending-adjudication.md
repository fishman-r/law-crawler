# 身份冲突与附件缺失的补充裁定

在已授权修复范围内处理 `pending` 时使用本流程。沿用[修复工作流](recovery-workflow.md)的冻结范围、执行和实际 OSS 审计；保留旧选择、旧待核结论和源目录值，不删除登记来重选任务。

## 区分问题和证据

| 情况 | 取证与处理 |
| --- | --- |
| 候选文种、编号（含后缀）、年份或主体不符 | 拒绝目标、来源、原件 SHA 三元配对，继续寻找正确来源。不仅凭同号同年接受，不撤回对其他目标合法的来源。 |
| OCR 误读 | 从已核验原件渲染相关页，实际查看正式标题、制令/签署栏和必要正文；不全局替换 O/0、I/1 或年份。 |
| 目录字段疑似错误 | 保存独立官方详情页或正文证据及 URL、SHA，逐字段记录原值、核实值和理由。不静默修改目录，不仅凭标题近似裁定。 |
| 原件自身矛盾 | 保留原字节及异常。只有排除错件、正式身份有充分证据时才批准身份；法律内容准确性保持未核验。 |
| 正文明确说附件未收录 | 核对声明所在页，另查官方附件入口。无法补齐时接收已核验正文并记 `incomplete`；不把未知情况等同于明确缺失。 |
| 页面链接到图片、附表或说明 | 解析实际相对 URL、跳转和响应类型，归档响应，核对与正文的关系。404、错误页和图片占位符不算附件。 |

保留影响意义的差异：否定词、国家或城市、机构或人员名称、任期、范围、修订条文号、文种及编号后缀。不要加入通用模糊同义词表。对印尼 `PENETAPAN PRESIDEN` 与 `KEPUTUSAN PRESIDEN` 分别核验，不能仅信任结构化文种字段。

在候选生成阶段对照来源完整正式标题与结构化文种；二者明确冲突时停止自动配对，保留原字段及合法原件供纠错。正文识别单独返回 `penpres`，不要把可读的文种冲突误报为扫描不可读。仅检查正式标题前缀，不将事项中引用的另一部法规当成本法规文种。

逐条裁定目录错字或简称时，联合核对正式文种、完整编号、年份、制定机关、正文主题及相关范围条款。允许使用独立于目标目录的官方原件作为佐证；不机械要求另一个网站重复证明，也不要求目录标题逐字等同于原件。记录哪些原值有误、哪些证据排除了另一法规，保持自动匹配严格，避免将个案纠错推广成模糊合并。

遇到旧式 `TAB`、`ENTER` 附件导航或 `TABULAR OR GRAPHIC MATERIAL ... NOT DISPLAYED` 占位时，继续检查后续页、PDF嵌入文件及URL注释；不要只审前两页。将占位词作为取证线索，仍以实际页图确认。报告“当前归档副本缺附件”；其他入口403或超时不能证明所有官方来源都没有附件。

## 保存补充审核

新建审核文件，沿用原选择、目标身份及原件绑定。只把新批准文件传给 `freeze`；将旧待核绑定进批准项 `evidence.pending_resolution`：

```json
{
  "kind": "ocr_transcription_error",
  "previous_review": {"path": "OLD_REVIEW.json", "sha256": "OLD_SHA"},
  "rationale": "说明新证据如何消除旧冲突，并限定核验范围",
  "evidence_files": [{"path": "NEW_EVIDENCE", "sha256": "NEW_SHA"}]
}
```

需要更换错误候选来源时，不复用旧选择中的 source，也不删除筛选登记。准备包含完整新候选文本及 SHA 的 JSON；每项增加 `previous_review: {path, sha256}`、`previous_selection: {path, sha256}`，绑定旧审核中的同一待核目标，保留原 `target_identity` 与 `target_title`。执行只读队列、独占锁下的新选择：

```text
PYTHON SKILL_DIR/scripts/recovery_workflow.py supplement --batch BATCH --manifest MANIFEST_JSON --manifest-sha256 MANIFEST_SHA --candidates REPLACEMENT_CANDIDATES_JSON --output WORK/replacement-01/selection.json
```

将 `WORK` 放在该批次目录内。该入口拒绝非待核目标、已完成目标、记录已改变、历史绑定不符及已拒绝的配对；保留旧选择登记，输出不能覆盖。候选选择不等于身份批准。实际检查新原件后，使用新的 selection 绑定生成补充审核，再按常规 freeze、执行与审计流程入库。确认目录文种或年份错误时通过 `corrected_fields` 记录核实值，不静默修改旧目标身份；仅同号同年或题名近似不能作出该裁定。

若在官方存储发现正确原件，但旧来源结果只保存了错误下载链接对应的文件，保留旧来源结果不变。先按实际响应保存原格式文件，独立核验正文，再用 `OSSSink.archive` 归档原件及 `{target_id, parent_source_id, role: "regulation_body", detail_url}` 来源元数据。不要把新正文伪装成附件，也不要直接追加到旧来源已完成结果。

在新审核的 repair 中明确提供 `replacement_primary: {role, receipt, metadata_sha256, evidence_report_path, evidence_report_sha256}`；receipt 包含完整捕获字段及 OSS 引用，唯一 `approved_primary_sha256s` 必须等于该新正文 SHA。独立报告的 `items` 绑定 `target_id`、`parent_source_id`、`target_identity`、`target_title`、`original_sha256`、`primary_identity_approved: true`、`role: "regulation_body"`、相同 `receipt` 与 `oss`。通过 `supplement` 选择后，在冻结、执行和审计阶段分别校验报告、官方来源域、实际OSS原件及元数据；旧来源记录/结果SHA仍须一致。保存对错链的逐字段裁定，不把猜测文件名当作成功下载的证据。

按类型补充字段：

- `ocr_transcription_error`：在 `evidence.evidence_pages` 绑定页图 `{path, sha256, original_sha256, page_number}`，页号从1开始；在 `viewed_image_sha256s` 记录实际看过的图片 SHA。其他补充裁定的页图也使用该格式；OCR 置信度不代替查看。
- `catalog_metadata_error`：在 resolution 的 `official_evidence` 保存 `{path, sha256, url}`，在 `corrected_fields` 列出 `{field, recorded_value, verified_value}`。作为可追溯纠错记录保留，不直接覆盖目录。
- `source_internal_anomaly`：在 `evidence.source_text_anomalies` 记录异常位置、原文及证据绑定；将 `legal_content_accuracy` 标记为 `not_verified`。
- `missing_attachment_confirmed`：设置 `evidence.known_missing_attachments` 和 `primary_text_identity_verified: true`；在 resolution 的 `missing_attachment_evidence` 逐项保存 `{description, quote, path, sha256}`，description 对应缺失清单，quote 为原文声明。图证据须实际查看；文字证据须实际读到声明。
- `attachment_recovered`：提供现有 `additional_verified_attachments` 完整证据链，包括正文父 SHA、独立审核、官方 URL、原件和元数据回读收据。不能仅改缺失标记；仍有缺失时继续按缺失状态交付。

将本入口限定为仍处于 `no_original` 的目标，同时补正文和已核验附件。已有 `incomplete` 任务转到[附件续收闭环](attachment-recovery.md)；保留待补清单及新取得的附件证据，不回退队列为 `no_original` 绕过限制，也不手工清空缺件标记。

由 `recovery_adjudication.py` 在冻结、执行前和审计中校验补充证据。哈希校验只证明证据未变，不证明官方权威性或裁定语义正确；保留独立审核职责。兼容旧审核读取，新补充裁定使用上述格式。

## 拒绝配对而不污染去重

把确定错配保存为批次 `recovery/rejected-candidate-pairs.json`：

```json
{
  "schema_version": 1,
  "batch_id": "BATCH_ID",
  "rejections": [{
    "scope": "candidate_pair",
    "target_id": "TARGET_SHA_ID",
    "source_id": "SOURCE_SHA_ID",
    "sha256": "ORIGINAL_SHA",
    "reason": "文种及主体冲突的具体说明",
    "review_path": "REVIEW.json",
    "review_sha256": "REVIEW_SHA"
  }]
}
```

先保存独立审核，再持有批次锁登记；已有登记先保存哈希绑定快照，再原子更新，不覆盖旧审核。筛选、冻结、执行前及审计检查配对。配对拒绝不阻止同一原件用于另一个合法目标，也不阻止原目标使用另一来源。

仅将来源原件本身确认无效的情况放入既有 `invalid-source-files.json`。历史 `selection.json` 仅用于目标防重，不是无效来源清单。

交付身份解除数、正文已收但附件仍缺数、实际补齐附件数、已拒错配数及剩余待核数，附逐项证据和 Markdown、HTML 报告。拒绝错配不增加完成数；身份确认不代表附件完整或法律效力确认。
