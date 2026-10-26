# 用有限 OCR 核验缺失原件候选

仅在原件缺少文本层、且已有官方来源的候选原件时调用 `law_crawler.dpr_ocr_evidence`。把 OCR 用作查证文种、编号、年份和完整事项标题的证据；不要把 OCR 置信度、文本相似度或识别成功本身当作归档批准。

## 准备与执行

1. 从冻结清单生成候选 manifest，逐条提供 `target_id`、`source_id`、`sha256`、`raw_key`、`size_bytes` 和含目录身份、完整标题的 `evidence`。
2. 安装运行环境官方仓库中的 `pdftoppm`、`tesseract` 及可用语言包。记录实际语言包；缺少印尼语包时明确记录采用 `eng`，不要声称已运行印尼语模型。
3. 在服务器执行；保持单路、`CPUQuota=50%`、`MemoryMax=384M`、`OMP_THREAD_LIMIT=1`。先验证五条真实样本及对应图片，再扩大证据采集。
4. 只读取 OSS 原件，流式回读并核对原件 SHA256 和大小。限制每份原件 32MiB、最多前两页、180dpi、最长边2400像素，分别对每页渲染和 OCR 设置45秒和60秒超时。传输最多三次退避重试。
5. 使用持久化的证据目录，不把正式证据留在系统临时目录。保留 `results.jsonl`、页码、渲染 PNG、图片 SHA256、OCR 全文及文本 SHA256、原件 SHA256、实际语言、逐条耗时和明确失败原因。

```sh
python -m law_crawler.dpr_ocr_evidence \
  --batch /deployment/batch \
  --manifest /deployment/batch/recovery/ocr/manifest.json \
  --output /deployment/batch/recovery/ocr/evidence \
  --credentials /deployment/secrets/oss.json \
  --language eng --limit 5
```

把路径替换为部署环境配置。`--batch` 指向含原 `plan.json` 的批次，OSS bucket、endpoint、region、prefix 从该计划读取，不使用生产默认值；存在 `plan.sha256` 时先校验签名。使用 `--limit 0` 处理清单全部候选。复用同一输出目录重跑，按目标、来源和原件 SHA256 跳过已记录结果；目录锁拒绝同目录并发执行。遇到需重新识别的失败项，另建有明确清单的新修订证据目录，保留上一轮结果。

## 复核与入库边界

- 把所有 OCR 结果保持为 `pending_independent_visual_review`，即使字符串恰好完全匹配。
- 逐图核对实际字形和阅读顺序，核对完整事项中的修订次数、引用法规、地名及数字；不要自动纠正年份、编号或删去限定词。
- 单独记录目录错拼、简称与全称差异以及 OCR 误读。批准时绑定实际核对的原件 SHA256、页码和图片 SHA256。
- 将通过独立视觉核验的候选清单冻结后交给原有修复计划执行器。重新检查来源和目标 outcome SHA256、源原件及元数据验证状态，再按既有 CAS、OSS 溯源元数据提交顺序入库。
- 保持原始文件、生产队列、下载状态和翻译流程不变。此脚本不写 OSS、不改队列、不启动全文 OCR 或翻译。

维护唯一源码于 `scripts/law_crawler/dpr_ocr_evidence.py`；将服务器 runtime 目录视为有版本标识的部署快照，不建立第二套可编辑源码。

## 复查超限的文字层候选

先核对候选实际字节数及服务器剩余空间。对已归档OSS、仅因32MiB核验上限跳过的PDF，使用`law_crawler.dpr_body_evidence --batch BATCH --max-pages 2 --max-mib 96`在独立证据目录重查；按实际大小选择显式上限，允许范围1至128MiB，默认仍为32MiB。保持顺序流式下载、SHA及大小回读核验、前两页提取和30秒提取超时，不加载全文或取消传输大小限制。保留此前超限结果与本次实际上限。此开关只用于文字层核验，不提高OCR脚本的32MiB限制；仍无文字层时另列待处理，不自动批准。
