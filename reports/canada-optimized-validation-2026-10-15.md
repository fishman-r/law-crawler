# 加拿大优化后skill两阶段验证 · 2026-10-15

> 本报告记录本次执行结果；下载成功不代表法律效力已核验。

## 执行信息

| 项目 | 值 |
| --- | --- |
| 报告版本 | 3 |
| 适用国家/法域 | Canada |
| 采集模式 | metadata\_then\_content\_validation |
| 执行结果 | passed\_with\_fixed\_replay\_limit\_defect |
| 记录时间（UTC） | 2026-10-15T01:57:23.227395+00:00 |
| 保存目录 | /Users/rpy/home/github/law-crawler/downloads/Canada |
| 索引 | /Users/rpy/home/github/law-crawler/countries/Canada/index.md |
| 批次 | {"content": "canada-content-validation-20261015", "metadata": "canada-metadata-validation-20261015"} |
| 执行范围 | 已登记的两条联邦法规发现链，各检查4个详情；总限量6、每平台限量2，英文有限样本，不是全量加拿大法规。法院、学术及GitHub说明引用均未混入法规库。 |
| 元数据阶段 | 8条候选及独立目录JSON，原文任务0、请求0、落地原文0。执行报告last-run.md不计Markdown法规原文；阶段证据在metadata-only-check中冻结。元数据批次重跑仍8条、0原件请求。 |
| 原文与转换 | 首轮实际请求4份司法部XML并转换Markdown；原XML字节、原格式及每原件metadata侧车保存在raw/，正文Markdown另有schema2文件元数据及转换溯源。国家库存最终7对，新增3来自保留的历史缺陷轮。 |
| 版本边界 | 官方当前合并文本端点已实际刷新；详情current-to为2026-09-21，与XML LIMS日期分别保留。8条公布日均未知、latest\_unverified=true，不能按页面修改日或最近修订日虚构最新公布版本，也不宣称全站唯一最新或法律效力已确认。适用地区缺证据保留null，任务法域Canada不替代地域证据。 |
| 去重 | A-1两条发现链指向已确认官方同资源/同版本观察，候选队列仅选一，另一条duplicate\_official\_resource；未请求别名不伪造成功来源，每原件当前实际source\_count1。全项刷新4次均content\_duplicate，原XML和Markdown哈希未变化、未新增正文；跨别名实际字节合并由离线回归验证。 |
| 发现并修复 | 默认重跑缺陷：前轮复用4成功项未计入额度，继续下载原限量跳过3项，结果7份超过原计划。缺陷轮0002和额外3原件原样保留。现已先验证所有有效成功项并预留额度，再处理失败/待执行项；超额历史按原冻结顺序限额显示，缺失损坏文件仍可修复，缓存复用报告按内容去重并补SHA及selection。 |
| 修复后验收 | 内容批次0003复用4份/0原文请求；0004全项刷新4次/4份content\_duplicate/0新增。各平台本轮均2份，冻结计划未改。国家历史库存7份与每轮4份分别报告。 |
| 文本核对 | 独立审查：7份XML根结构/标题/类型/编号与元数据匹配；68,654个XML文本原子规范化后均在Markdown找到，缺失0。双SHA、字节、原件侧车与SQLite一致。这是文本保留与结构核对，不等同于排版、交叉引用、body\_review人工登记、法律效力或全量覆盖验收。 |
| 报告验收 | 各阶段JSON/Markdown/HTML已生成；固定离线HTML结构、转义及引用检查通过。file://浏览器预览被URL策略阻止，未绕过，浏览器视觉验收未完成。 |
| 代码及skill | 修复regulation\_pipeline.py并同步工程skill/scripts副本；新增tests/test\_batch\_replay\_limits.py五个端到端用例；更新batch-workflow、canada及README并同步本机安装版。 |
| 验证 | 210项全量单元测试通过；独立审计73项通过且blockers=\[\]；sync\_skill --check与quick\_validate通过。 |
| 分工 | 主代理：两阶段采集、发现/修复重跑限量、实际重跑及汇总报告；canada\_validation\_review审查子代理：计划/文件/转换/报告73项独立只读核对；replay\_limit\_tests测试子代理：5项离线端到端回归。 |
| 审计证据 | \["/Users/rpy/home/github/law-crawler/reports/canada-baseline-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/canada-metadata-only-check-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/canada-content-before-replay-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/canada-content-before-refresh-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/canada-independent-audit-2026-10-15.json"\] |

## 数量汇总

| 状态 | 数量 |
| --- | --- |
| 目录候选 | 8 |
| 元数据阶段原件请求 | 0 |
| 首次不同原件 | 4 |
| 问题轮额外保留 | 3 |
| 当前库存XML | 7 |
| 当前库存Markdown | 7 |
| 正文文件JSON | 7 |
| 原件metadata侧车 | 7 |
| 修复后默认复用 | 4 |
| 修复后刷新重复 | 4 |
| 刷新新增 | 0 |
| 候选重复别名 | 1 |
| 公布日待确认 | 8 |
| 全量回归测试 | 210 |
| 独立审查项 | 73 |

## 法规目录与元数据

| 法规条目标识 | 官方平台 | 法规名称 | published\_at | consolidated\_to | updated\_at | effective\_region | 最新版本选择 | 详情来源 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ef0d176402a86ac0d67b5680124e2f630398685428809e692148af4333707e4a | open.canada.ca | Access to Information Act ( R.S.C. , 1985, c. A-1) |  | 2026-09-21 | 2026-06-14 |  | {"latest\_unverified": true, "reason": "same\_official\_resource\_date\_unknown", "selected": true, "selected\_id": "ef0d176402a86ac0d67b5680124e2f630398685428809e692148af4333707e4a"} | https://laws-lois.justice.gc.ca/eng/acts/A-1/index.html |
| 379c69441615021ed923f5ff7456463150a8697aabffe7ae2cc7b707e021a298 | open.canada.ca | “MV Sonia” Remission Order, 2007 ( SOR /2007-151) |  | 2026-09-21 | 2007-06-14 |  | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "379c69441615021ed923f5ff7456463150a8697aabffe7ae2cc7b707e021a298"} | https://laws-lois.justice.gc.ca/eng/regulations/SOR-2007-151/index.html |
| aa5463bd5a5e3da996bf299cad986034873fcfe409cd2889c261b9d51e58e5a4 | open.canada.ca | Accessible Canada Act ( S.C. 2019, c. 10) |  | 2026-09-21 | 2023-04-27 |  | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "aa5463bd5a5e3da996bf299cad986034873fcfe409cd2889c261b9d51e58e5a4"} | https://laws-lois.justice.gc.ca/eng/acts/A-0.6/index.html |
| 362c7714ff1d70824e134d6321a8d81bb43f37e6d9688a7ea39a2cd329cff64c | open.canada.ca | “SeaRose FPSO” Remission Order, 2004 ( SOR /2004-96) |  | 2026-09-21 | 2006-03-22 |  | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "362c7714ff1d70824e134d6321a8d81bb43f37e6d9688a7ea39a2cd329cff64c"} | https://laws-lois.justice.gc.ca/eng/regulations/SOR-2004-96/index.html |
| e5333f665b614bca3421e8950e6033f81d22da7fe2c9fc82a48b2670bfa10bbc | laws.justice.gc.ca | Access to Information Act ( R.S.C. , 1985, c. A-1) |  | 2026-09-21 | 2026-06-14 |  | {"latest\_unverified": true, "reason": "duplicate\_official\_resource", "selected": false, "selected\_id": "ef0d176402a86ac0d67b5680124e2f630398685428809e692148af4333707e4a"} | https://laws.justice.gc.ca/eng/acts/A-1/index.html |
| e59ecb840ffd14c002fd7529d8e5ffa1f8710498e9091cfc263608a50f28ce3a | laws.justice.gc.ca | Canada Occupational Health and Safety Regulations ( SOR /86-304) |  | 2026-09-21 | 2025-03-26 |  | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "e59ecb840ffd14c002fd7529d8e5ffa1f8710498e9091cfc263608a50f28ce3a"} | https://laws.justice.gc.ca/eng/regulations/SOR-86-304/index.html |
| 4ef5ffe06d9e7237efd91ffaf008ca46d98f1406c308c917a6a79a2da0fd0795 | laws.justice.gc.ca | Canada Elections Act ( S.C. 2000, c. 9) |  | 2026-09-21 | 2026-09-16 |  | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "4ef5ffe06d9e7237efd91ffaf008ca46d98f1406c308c917a6a79a2da0fd0795"} | https://laws.justice.gc.ca/eng/acts/E-2.01/index.html |
| db9a9ab83f95a7803e08b6061a8b02b7c87425193f74d4a4389d063b05491c6d | laws.justice.gc.ca | Canadian Aviation Regulations ( SOR /96-433) |  | 2026-09-21 | 2026-06-17 |  | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "db9a9ab83f95a7803e08b6061a8b02b7c87425193f74d4a4389d063b05491c6d"} | https://laws.justice.gc.ca/eng/regulations/SOR-96-433/index.html |

## 逐文件执行状态

| 批次编号 | 执行轮次 | 采集模式 | 状态 | 元数据条目数 | 原件文件数 | counts | original\_requests | error\_count | 原因 | markdown\_report | html\_report |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| canada-metadata-validation-20261015 | 1 | metadata | completed | 8 | 0 | {} | 0 | 0 | 仅元数据，无原文请求 | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-metadata-validation-20261015/attempts/0001/report.md | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-metadata-validation-20261015/attempts/0001/report.html |
| canada-metadata-validation-20261015 | 2 | metadata | completed | 8 | 0 | {} | 0 | 0 | 仅元数据，无原文请求 | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-metadata-validation-20261015/attempts/0002/report.md | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-metadata-validation-20261015/attempts/0002/report.html |
| canada-content-validation-20261015 | 1 | content | completed | 8 | 4 | {"downloaded": 4} | 4 | 0 | 按冻结计划及本轮状态核对 | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0001/report.md | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0001/report.html |
| canada-content-validation-20261015 | 2 | content | completed | 8 | 7 | {"downloaded": 3, "previous\_success": 4} | 3 | 0 | 历史缺陷轮：缓存未计额度，超额3份，已保留并修复 | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0002/report.md | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0002/report.html |
| canada-content-validation-20261015 | 3 | content | completed | 8 | 4 | {"previous\_success": 4} | 0 | 0 | 按冻结计划及本轮状态核对 | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0003/report.md | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0003/report.html |
| canada-content-validation-20261015 | 4 | content | completed | 8 | 4 | {"content\_duplicate": 4} | 4 | 0 | 按冻结计划及本轮状态核对 | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0004/report.md | /Users/rpy/home/github/law-crawler/downloads/.batches/canada-content-validation-20261015/attempts/0004/report.html |

## 下载文件

| 法规名称 | 本地文件 | original\_file | 元数据文件 | 内容SHA-256 | original\_sha256 | bytes\_markdown | bytes\_xml | source\_count | consolidated\_to | last\_amended\_at | published\_at | updated\_at | effective\_region | 状态 | 原件来源 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| “MV Sonia” Remission Order, 2007 ( SOR /2007-151) | /Users/rpy/home/github/law-crawler/downloads/Canada/3ca714dcb0688ed4f2f5ab94b93d308d225039ec6bcd78a21eb161494441c393.md | /Users/rpy/home/github/law-crawler/downloads/Canada/raw/3ca714dcb0688ed4f2f5ab94b93d308d225039ec6bcd78a21eb161494441c393.xml | /Users/rpy/home/github/law-crawler/downloads/Canada/3ca714dcb0688ed4f2f5ab94b93d308d225039ec6bcd78a21eb161494441c393.json | 31b5436153ff0a98e7ce253a5a46813eeb81fbb3d3b31731592e0c0a415def01 | 3ca714dcb0688ed4f2f5ab94b93d308d225039ec6bcd78a21eb161494441c393 | 1195 | 3875 | 1 | 2026-09-21 |  |  | 2007-06-14 |  | first\_run\_download | https://laws-lois.justice.gc.ca/eng/XML/SOR-2007-151.xml |
| Accessible Canada Act ( S.C. 2019, c. 10) | /Users/rpy/home/github/law-crawler/downloads/Canada/50b57cf283b3b8a1830084c680bab980005b48a55f0c5b0ebebaffc76c3626a2.md | /Users/rpy/home/github/law-crawler/downloads/Canada/raw/50b57cf283b3b8a1830084c680bab980005b48a55f0c5b0ebebaffc76c3626a2.xml | /Users/rpy/home/github/law-crawler/downloads/Canada/50b57cf283b3b8a1830084c680bab980005b48a55f0c5b0ebebaffc76c3626a2.json | 7ed95079a104cabf710b02d165bb0a0169fddd1a7817c6fc69dfe7c1c142c8d0 | 50b57cf283b3b8a1830084c680bab980005b48a55f0c5b0ebebaffc76c3626a2 | 150054 | 376312 | 1 | 2026-09-21 | 2023-04-27 |  | 2023-04-27 |  | historical\_over\_limit\_download\_retained | https://laws-lois.justice.gc.ca/eng/XML/A-0.6.xml |
| Canada Occupational Health and Safety Regulations ( SOR /86-304) | /Users/rpy/home/github/law-crawler/downloads/Canada/56876b7d3830941705abe912e3b32caf92ee6846b1bcb0de5ad7acda697683c9.md | /Users/rpy/home/github/law-crawler/downloads/Canada/raw/56876b7d3830941705abe912e3b32caf92ee6846b1bcb0de5ad7acda697683c9.xml | /Users/rpy/home/github/law-crawler/downloads/Canada/56876b7d3830941705abe912e3b32caf92ee6846b1bcb0de5ad7acda697683c9.json | 50d999de4e5df20c78c9684d2bfd39f9ec52c88471c6b6cf6baf8a5a2ebab436 | 56876b7d3830941705abe912e3b32caf92ee6846b1bcb0de5ad7acda697683c9 | 390269 | 979314 | 1 | 2026-09-21 | 2025-03-26 |  | 2025-03-26 |  | first\_run\_download | https://laws.justice.gc.ca/eng/XML/SOR-86-304.xml |
| Canadian Aviation Regulations ( SOR /96-433) | /Users/rpy/home/github/law-crawler/downloads/Canada/628e52f0762affec2a160758afdd60152877d58f3b9acc1ee99a4700ba1a5a9b.md | /Users/rpy/home/github/law-crawler/downloads/Canada/raw/628e52f0762affec2a160758afdd60152877d58f3b9acc1ee99a4700ba1a5a9b.xml | /Users/rpy/home/github/law-crawler/downloads/Canada/628e52f0762affec2a160758afdd60152877d58f3b9acc1ee99a4700ba1a5a9b.json | e1f507cb5612a3f32ecd591ce5ac0c526b1e2b7191449ff63148580c2bd6edee | 628e52f0762affec2a160758afdd60152877d58f3b9acc1ee99a4700ba1a5a9b | 1729803 | 4495476 | 1 | 2026-09-21 | 2026-06-17 |  | 2026-06-17 |  | historical\_over\_limit\_download\_retained | https://laws.justice.gc.ca/eng/XML/SOR-96-433.xml |
| Access to Information Act ( R.S.C. , 1985, c. A-1) | /Users/rpy/home/github/law-crawler/downloads/Canada/ac631ba9e6a1bdaa997464176f631ccd2a05d3e6225cb68503e0cff0cec78847.md | /Users/rpy/home/github/law-crawler/downloads/Canada/raw/ac631ba9e6a1bdaa997464176f631ccd2a05d3e6225cb68503e0cff0cec78847.xml | /Users/rpy/home/github/law-crawler/downloads/Canada/ac631ba9e6a1bdaa997464176f631ccd2a05d3e6225cb68503e0cff0cec78847.json | 71acf3206ac7d4e9aae9cd53f9cce8be7ed76db62fb530d3c889760e379ef906 | ac631ba9e6a1bdaa997464176f631ccd2a05d3e6225cb68503e0cff0cec78847 | 202797 | 488623 | 1 | 2026-09-21 | 2026-06-14 |  | 2026-06-14 |  | first\_run\_download | https://laws-lois.justice.gc.ca/eng/XML/A-1.xml |
| Canada Elections Act ( S.C. 2000, c. 9) | /Users/rpy/home/github/law-crawler/downloads/Canada/b29737e36630460e3641ddeb69afa8598517d4d7063d3aa889790b28bededf4b.md | /Users/rpy/home/github/law-crawler/downloads/Canada/raw/b29737e36630460e3641ddeb69afa8598517d4d7063d3aa889790b28bededf4b.xml | /Users/rpy/home/github/law-crawler/downloads/Canada/b29737e36630460e3641ddeb69afa8598517d4d7063d3aa889790b28bededf4b.json | 78d6a7108c0869102e3b14383acbd2a41e9fe313dc4b4efb40fbc150dc2d23a4 | b29737e36630460e3641ddeb69afa8598517d4d7063d3aa889790b28bededf4b | 1070842 | 2461283 | 1 | 2026-09-21 | 2026-09-16 |  | 2026-09-16 |  | first\_run\_download | https://laws.justice.gc.ca/eng/XML/E-2.01.xml |
| “SeaRose FPSO” Remission Order, 2004 ( SOR /2004-96) | /Users/rpy/home/github/law-crawler/downloads/Canada/dae0538f1a0189a33ed5979f709c27445ec652ed833ce141e1fe3d2e9d6a0e93.md | /Users/rpy/home/github/law-crawler/downloads/Canada/raw/dae0538f1a0189a33ed5979f709c27445ec652ed833ce141e1fe3d2e9d6a0e93.xml | /Users/rpy/home/github/law-crawler/downloads/Canada/dae0538f1a0189a33ed5979f709c27445ec652ed833ce141e1fe3d2e9d6a0e93.json | d1844925d4e20467050f2799ed3d75b90db49f550a2b5b3f4a5e41671d7c408a | dae0538f1a0189a33ed5979f709c27445ec652ed833ce141e1fe3d2e9d6a0e93 | 1195 | 3923 | 1 | 2026-09-21 |  |  | 2006-03-22 |  | historical\_over\_limit\_download\_retained | https://laws-lois.justice.gc.ca/eng/XML/SOR-2004-96.xml |

## 错误与原因

| 状态 | 原因 |
| --- | --- |
| fixed\_historical\_defect | content attempt0002默认重跑超额；原记录保留，已修复并通过后续实跑及5项回归 |
| visual\_report\_review\_not\_completed | 浏览器URL策略阻止file://报告预览；未绕过，仅完成离线HTML结构核对 |

## 平台结果

| 官方平台 | 国家 | 状态 | 检查详情页数 | 错误记录 | 记录时间（UTC） | file\_count |
| --- | --- | --- | --- | --- | --- | --- |
| open.canada.ca | Canada | metadata\_collected | 4 | \[\] | 2026-10-15T01:48:02.936973+00:00 | 0 |
| laws.justice.gc.ca | Canada | metadata\_collected | 4 | \[\] | 2026-10-15T01:48:02.936978+00:00 | 0 |
| scc-csc.gc.ca |  | out\_of\_scope\_case\_law |  |  |  |  |
| decisions.scc-csc.ca |  | out\_of\_scope\_case\_law |  |  |  |  |
| scc-csc.lexum.com |  | out\_of\_scope\_case\_law |  |  |  |  |
| decisions.fct-cf.ca |  | out\_of\_scope\_case\_law |  |  |  |  |
| arxiv.org |  | out\_of\_scope\_academic |  |  |  |  |
| github.com |  | reference\_only |  |  |  |  |

