# 显式运行配置与旧批次兼容

将国家、来源范围、时间截点和存储位置作为任务输入，不从当前机器、历史任务、默认 Bucket 或固定条数推断。

## 新批次

- 明确提供已登记的 `--jurisdiction`。全量目录归档默认读取完整 skill 内的国家索引；需要工作区自有索引时指定 `--registry-root`。核对来源适配器所属国家和该法域索引，未适配来源报错，不套用其他国家解析规则。
- 从 `--census-root` 顶层冻结 `plan.json` 的 `sources` / `platforms` 取得来源，或重复指定 `--source` 明确子集。要求每个所选来源已有子计划和 `records.sqlite3`；缺库不能静默忽略。保留平台别名和官方域名作为适配规则。
- 根据所选目录的实际记录计算队列总量。需要校验用户授权数量时提供可选 `--expected`；它是数量断言，不是通用默认值。目录条数不等于唯一法规数或截止日内全部版本数。
- 显式提供 `--bucket`、`--endpoint`、`--region`、`--prefix`。为国家选择正确的 prefix，不自动写入历史印尼目录。凭据放工程外，通过 `--credentials` 传入；`--freeze-only` 不需要凭据或网络访问。
- 按需提供 `--old-metadata` 导入成功缓存。没有旧缓存时可省略，不要求使用旧工程数据。
- 为新目录普查提供明确 `--cutoff YYYY-MM-DD`；允许从已有根计划或平台计划继承同一日期。日期缺失、非法或冲突时停止，不能自动采用今天或历史固定日期。

```text
PYTHON -m law_crawler.census_to_oss --jurisdiction JURISDICTION --census-root CENSUS_ROOT --output NEW_BATCH --bucket BUCKET --endpoint ENDPOINT --region REGION --prefix COUNTRY_PREFIX --freeze-only
```

从完整 skill 的 `scripts` 路径设置 `PYTHONPATH` 后在任意目录运行。先检查冻结计划与实际总量，再使用同一 `--output BATCH --credentials CREDENTIALS_JSON` 执行。可用 `--limit` 做有界验收；不凭配置化宣称每个国家的目录发现与全部版本已经适配。

## 续跑与资源阈值

从已签名的 `plan.json` 继承法域、OSS 配置、来源及总量；显式重传的冲突参数须拒绝。保留旧计划原始字节及 SHA，不把历史 `53430`、旧截点或旧来源改写成新配置。需要改变范围、目标存储或已冻结资源参数时创建新批次。

新计划冻结 `workers`、`max_attempts`、`max_file_bytes`、`detail_max_bytes`、`min_free_bytes`、`timeout`、`backoff_base` 和 `host_delay`。默认值是资源策略，不是某台机器或某个国家的身份；执行遵守实际冻结值，不再以额外写死阈值覆盖计划。旧计划缺少的新资源字段使用兼容策略，不反向修改其签名。

`OSSSink` 不再提供生产 Bucket、Endpoint、Region 或 Prefix 默认值。正文/OCR 证据命令使用 `--batch` 从已有计划取得存储配置；存在 `plan.sha256` 时先验签。历史调用者需明确配置，不因缺参数而静默连接旧生产库。

区分平台协议常量、历史证据与运行配置：保留官方域名、文种规则、国家别名及已有报告中的日期和统计数字；只移除它们作为新任务隐式运行参数的行为。
