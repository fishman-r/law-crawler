# 已有正文的附件续收

在任务为 `incomplete`、正文身份已有明确审核结论且存在 `metadata.known_missing_attachments` 时，使用本流程。保留原正文、已收附件、目录记录和来源任务；不要回退状态或删除成功文件重跑。其他原因导致的不完整任务仍按原下载流程处理。

## 准备附件与审核

1. 从官方入口取得缺失附件，沿用原始响应归档、最多三次总尝试及 OSS 回读校验。已有成功原件直接复用，不重新请求正文。没有取得附件时保留缺件和失败原因，不生成批准项。
2. 独立核对附件与正文的关系、语言和完整范围。复用[待核裁定](pending-adjudication.md)的 `additional_verified_attachments` 格式，绑定附件原字节、正文 SHA、官方来源、附件证据报告及 OSS 元数据。`source_id` 指向已完成归档的官方来源任务；附件 URL 必须符合该来源适配器的官方主机范围。
3. 新建审核 JSON，提供 `reviewer`、`review_note` 和 `repairs`。每项包含 `target_id`、`source_id`、`approved_primary_sha256s`、`additional_verified_attachments` 和 `resolved_missing_attachments`。
4. 将 `resolved_missing_attachments` 每项写为 `{description, attachment_sha256s, fully_resolved, coverage_note}`。使用旧缺件描述的确切值；以 `coverage_note` 解释附件覆盖的具体部分。只补到部分材料时设置 `fully_resolved: false`，保留原缺件项。三语协定、两部条约、附表 I/II 等聚合描述须全部覆盖才允许设为 `true`，不能根据文件数量自动判断。
5. 在每个新附件的独立报告 `items` 条目中提供 `missing_attachment_coverage: [{description, attachment_sha256s, fully_resolved, coverage_note}]`，与审核中涉及该附件的映射一致。不要仅声明附件身份正确却省略覆盖判断。分轮取得附表 I、II 时，后一轮覆盖映射可同时引用已收附件和新附件 SHA，不能引用正文充数；每项至少包含一个新附件，旧附件和旧审核保持不变。

## 冻结、执行与验收

替换下列占位符为实际绝对路径。选择独立的 `WORK` 目录，逐次结果与报告使用新文件名；从任意当前目录运行完整 skill，无须工程根目录。

```text
PYTHON SKILL_DIR/scripts/attachment_recovery.py freeze --batch BATCH --review WORK/review.json --review-sha256 REVIEW_SHA --run-id attachment-repair-01 --output WORK/plan.json
PYTHON SKILL_DIR/scripts/attachment_recovery.py execute --batch BATCH --plan WORK/plan.json --plan-sha256 PLAN_SHA --credentials CREDENTIALS_JSON --output WORK/attempt-01.json
PYTHON SKILL_DIR/scripts/attachment_recovery.py audit --batch BATCH --plan WORK/plan.json --plan-sha256 PLAN_SHA --credentials CREDENTIALS_JSON --output WORK/audit-01.json
```

从冻结输出的 `plan.json.sha256` 取得 `PLAN_SHA`。从原批次 `plan.json` 读取 bucket、endpoint、region、prefix，凭据保持工程外文件。使用批次锁避免与其他下载写入进程并行；该入口串行追加已归档附件，不启动正文下载、OCR或翻译。

冻结旧目标结果、目录记录、来源结果、审核与附件证据的哈希。执行时重新校验绑定并实际回读 OSS 文件，先写入不可覆盖的元数据快照，再更新目标结果与队列。保留提交准备记录以恢复文件落盘与 SQLite 提交之间的中断。重跑沿用同一计划和 run-id、另选新的 attempt 文件，不覆盖已执行结果。

全部已知缺件得到覆盖才升级为 `completed`；否则维持 `incomplete`。保留旧缺件、每次解决范围和剩余缺件的历史，保持原 `all_attachments_verified` 结论，不以解决已知缺件宣称全版本或全部附件覆盖。

使用审计命令检查当前库状态与冻结计划是否一致，实际 GET 原件及元数据，并核对队列计数和对象登记；输出 JSON、Markdown、HTML。该审计不是对单次 attempt 文件的验收，后续计划改变目标后，旧计划的当前状态审计可能不通过；保留当时的不可变报告，不覆盖历史结果。

线上验证时，区分真实生产补齐与隔离模拟状态验收。允许使用已独立核验的真实附件及真实 OSS 字节，在独立队列中测试部分补齐、全部补齐、重跑和故障恢复；不得把隔离验收计入生产完成数。
