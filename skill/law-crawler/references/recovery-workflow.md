# 可重放的原件缺口修复

在用户已授权补收的批次内使用本流程。沿用[原件缺口修复](bulk-collection.md#修复未发现原件)的身份、附件及来源约束。仅生成候选不等于审核通过，审计通过也不等于整个法域或全部历史版本已经收齐。

## 准备输入

- 指定完整 skill 的 `SKILL_DIR`、批次目录 `BATCH` 和独立输出目录 `WORK`。从任意当前目录调用 `SKILL_DIR/scripts/` 下的入口，不依赖工程根目录或特定 agent。
- 提供已冻结的目标清单及其 SHA256。清单包含 `batch_id` 和 `jobs`，每个 job 的首项是目标 ID；沿用原清单，不用当前剩余任务重新缩小范围。
- 提供已完成原件取证的候选 JSON，及全部既有筛选目录和额外排除清单。保留原件 SHA、完整提取文本及对应 SHA；扫描图证据保留实际页图与 SHA。
- 使用新的输出路径。候选、审核、冻结计划、每次执行结果和审计报告均保留历史版本；修正证据后新建文件，不覆盖旧审核结论。

## 执行边界

对已有待核目标换用新来源时，转到[待核裁定](pending-adjudication.md)的 `supplement` 入口；普通 `select` 仍按历史目标登记排重，不通过删登记重跑。

1. 用 `recovery_workflow.py select` 选择仍为 `no_original` 的候选，排除已选目标并检查文本完整性。锁冲突时停止，不能绕过锁同时选择同一批次。筛选不访问 OSS，也不改变队列。
2. 由独立审核者实际阅读原件证据，输出 `repairs`、`pending` 和必要的拒绝理由。仅在 `repairs` 中列入批准的目标与来源，并显式限定 `approved_primary_sha256s`。按原件缺口修复约束记录明确缺附件及身份冲突；脚本不代替人工或模型作法律身份判断。
3. 用 `recovery_workflow.py freeze` 合并明确指定的审核文件。绑定目标清单、审核及证据文件、目标与来源记录和结果的 SHA，生成供现有执行器使用的计划。重复或冲突的批准项必须处理后再冻结；状态已变的条目单列跳过，不重新下载。
4. 用 `recover_missing_original.py` 执行冻结计划，使用最多 5 路并发及工程外的 OSS 凭据文件。每次尝试指定新的结果路径，保留旧结果；同一计划重跑只续补仍符合条件的目标。
5. 用 `recovery_audit.py` 只读审计实际执行结果。实际回读 OSS 原件与元数据，核对字节 SHA、文件大小、队列、目标结果、来源结果和审核证据。失败保留报告并返回非零状态，不自动改成成功或修正生产队列。

## 命令与审核文件

替换下列大写占位符为绝对路径或实际值。`PYTHON` 使用完整 Python 3.10+；OSS 执行与审计另需 `oss2`，凭据文件须为权限 600 或更严格的普通文件。不要把密钥写进命令、计划或报告。

```text
PYTHON SKILL_DIR/scripts/recovery_workflow.py select --batch BATCH --manifest MANIFEST_JSON --manifest-sha256 MANIFEST_SHA --candidates CANDIDATES_JSON --limit 20 --exclude-review-dir WORK --exclusion-manifest EXTRA_EXCLUSIONS_JSON --output WORK/review-01/selection.json
PYTHON SKILL_DIR/scripts/recovery_workflow.py freeze --batch BATCH --manifest MANIFEST_JSON --manifest-sha256 MANIFEST_SHA --review WORK/review-01/reviewed.json=REVIEW_SHA --run-id repair-unique-id --output WORK/repair-unique-id/plan.json
PYTHON SKILL_DIR/scripts/recover_missing_original.py --batch BATCH --target-manifest MANIFEST_JSON --repair-plan WORK/repair-unique-id/plan.json --credentials CREDENTIALS_JSON --workers 5 --results-output WORK/repair-unique-id/attempt-01.json
PYTHON SKILL_DIR/scripts/recovery_audit.py --batch BATCH --manifest MANIFEST_JSON --plan WORK/repair-unique-id/plan.json --results WORK/repair-unique-id/attempt-01.json --credentials CREDENTIALS_JSON --output WORK/repair-unique-id/audit-01.json
```

提前创建 `WORK`；无额外排除清单时省略该参数。重复 `--exclude-review-dir`、`--exclusion-manifest` 或 `--review PATH=SHA` 传入多个来源。把筛选文件命名为 `selection.json`，放在批次目录或明确指定的审核目录内。批次 `recovery/selection-registry/` 登记已选目标，跨不同输出目录自动防重；未完成的登记明确报错，先核查中断原因和输出文件，不删除登记绕过排重。

审核 JSON 使用 `repairs` 列出批准项，`pending` 列出待核项。每个批准项保留 `target_id`、`source_id`、`approved_primary_sha256s`，以及 `evidence` 中的 `review_status`、`reviewer`、`review_note`、`target_identity`、`target_title`、`original_sha256`、`original_key`、`original_verified`、`selection_path`、`selection_sha256`。这些身份、原件与选择字段须对应实际证据，不自行生成核验标记。文本审核另绑定全部保留页的 `extracted_page_sha256`；图像审核列出 `evidence_pages` 中每张图的路径和 SHA，并记录实际看过的 `viewed_image_sha256s`。明确缺附件时使用原件缺口修复文档定义的字段。

不要在同一次冻结中同时传入同一目标的旧待核与新批准文件；保留旧文件，但分开冻结已裁定结果，避免冲突。合批不是重新审核。`--results-output` 对应本次尝试，重跑时改为 `attempt-02.json` 等新路径；原计划不变。被 `--limit`、停止或鉴权失败阻止调度的任务明确记为 `not_scheduled`，审计不能把它们算作通过。进程崩溃时可能仅有可变 checkpoint，没有最终结果，应检查实际状态后续跑，不把缺失报告当成成功。

## 交付与限制

交付冻结计划、逐次执行结果和审计 JSON、Markdown、HTML 报告。分别报告已完成、明确缺附件的不完整、失败、跳过和未执行；显示原始目标清单和全批次的状态计数。不得把通过本轮审计的条数作为原始清单全部完成的证明。

先在隔离批次中验证新流程，再对已执行的真实批次进行只读 OSS 审计。用户要求线上验证时，所有运行、测试和 OSS 读取均在目标服务器进行；本地仅维护源码。不要在验证时启动生产下载器或自动部署 systemd 单元。
