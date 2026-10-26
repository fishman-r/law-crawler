# 印尼目录普查与全量计数

在用户要求“遍历全部、统计全量、核对覆盖率”时，先冻结法域、索引平台集合、截至日期、版本范围和仅元数据模式至批次 `plan.json`。沿用用户指定的线上执行环境；不要因此启动原件下载、OCR或翻译。

从当前完整 skill 的 `scripts` 目录运行模块，或将该目录设置为 `PYTHONPATH`。将 `CENSUS_ROOT` 替换为新批次绝对路径，显式传入用户要求的截止日。首次写入顶层计划后再启动各来源；同一平台仅允许一个写入进程。

```sh
python -m law_crawler.catalog_census_trade --root CENSUS_ROOT --previous-state PRIOR_ARCHIVE_STATE --cutoff YYYY-MM-DD
python -m law_crawler.catalog_census_ministries --root CENSUS_ROOT --platform kemkes.go.id --cutoff YYYY-MM-DD
python -m law_crawler.catalog_census_judiciary --root CENSUS_ROOT --platform dpr --cutoff YYYY-MM-DD
python -m law_crawler.catalog_census_national --root CENSUS_ROOT --platform peraturan.go.id --cutoff YYYY-MM-DD
python -m law_crawler.catalog_census_report --root CENSUS_ROOT --out REPORT_DIR
```

通过各模块 `--help` 选择已实现的平台；对其余注册国家使用对应适配器，不把印尼解析规则套用到其他国家。贸易部使用先前归档详情元数据补充身份，不重新下载原件。

## 分页与证据

- 对贸易部核对官方总数、页数和每页实际卡片，保留所有详情链接。
- 对DPR沿官方文种和 `/year/YYYY` 年份链接遍历；保留没有详情链接的目录行至独立清单，不伪造详情地址。
- 对财政部读取官方类型分组后分别遍历，避免全站搜索结果上限截断。
- 对卫生部使用真实目录分页，仅从标题链接提取卡片，防止“详情”按钮覆盖标题/类型。
- 对投资部使用官网公开的目录API；对金融监管局保留SharePoint表单状态执行真实下一页操作，并单独冻结官网导航链接的JDIH补充计划（`--platform ojk.go.id-jdih-supplement`）。核对完整DataTables响应和导航分类分支，别把中央列表完成当成全平台完成。
- 对生产入口超时或403，记录正常官方备用入口及普通浏览器结果；不要把实验/开发站数字当生产平台总数。资源受限主机串行启动浏览器。
- 保存每次目录响应、来源地址、抓取时间、哈希、分页断点及 `records.sqlite3`。复用哈希有效的目录缓存续跑；新截点另建批次，不在旧计划中改日期。
- 将同详情URL的不同官方ID/元数据变体全部保留；URL去重只减少存储重复，不能抹掉版本线索。

## 计数与验收

把原始目录总数明确标为截止日筛选前，另列截止日内、之后和日期未知的范围，禁止将晚于截点的材料计作截点内已确认法规。分别报告官方显示目录总数、实际遍历目录项、独立详情URL、无链接行、法规身份组、待分类材料和已确认版本数。不要把首页统计、抓取页数或下载文件数当成法规数量。

仅对明确的完整法域、机关、文种、年份、番号进行身份合并；别名跨源归一化未经核实前保留未知。保留修订、废止与历史记录，不套用“仅最新版本”过滤。全版本完整性需要官方版本清单证据；同法规多个文件不自动构成多个版本。

将草案、立法计划、个案裁判与正式规范分列。将Keputusan规范性未知项保留为待核验，不仅凭标题提及草案或Keputusan排除正式法规。

使用明确公布/发布日核对截止日；分别标记网页日期含义不明、仅年份、日期缺失及截点之后的记录。不要用制定日或更新时间替代公布日。

有任一平台目录无法遍历、总数未对账、历史版本未核实或截点范围未确认时，将“全部法规全部版本精确总量”保留为null。输出JSON、Markdown和HTML，并在首屏说明未知原因；允许展示已观察到的目录规模，禁止声明100%覆盖。
