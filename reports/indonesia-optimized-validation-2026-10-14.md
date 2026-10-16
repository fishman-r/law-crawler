# 印尼优化 Skill 两阶段验证 · 2026-10-14

> 本报告记录本次执行结果；下载成功不代表法律效力已核验。

## 执行信息

| 项目 | 值 |
| --- | --- |
| 适用国家/法域 | Indonesia |
| 执行范围 | 按现有index.md路由全部11个平台，每平台最多2个详情/2份原文；有界样本，不代表全量覆盖。 |
| 保存目录 | /Users/rpy/home/github/law-crawler/downloads/Indonesia |
| 批次编号 | \["indonesia-metadata-validation-20261014", "indonesia-content-validation-20261014"\] |
| 执行结论 | 两阶段、原始PDF保存、逐文件元数据、多层去重、默认重跑、刷新重跑及两种报告已实测；平台访问与正文核验仍有未通过项。 |
| 版本选择验证 | 目录16条分别为16个独立法规身份，12条有明确公布日，4条MA/OJK日期未知。按本批候选公布日选择的重算结果一致；没有真实同身份新旧版本竞争样本，竞争规则由离线回归验证。不能宣称这16条是全站最新现行版本。下载文件中10条公布日已知、4条未知。 |
| Markdown正文转换 | 真实印尼原文全部为PDF，按原格式保留；正文Markdown转换在线不适用。HTML/XML/JSON转换、原响应保留及独立元数据由离线回归覆盖。Markdown执行报告已经生成，与法规正文转换不同。 |
| 正文审查 | 输出审查子代理用pypdf核对首两页；12份标题/类型/编号年份匹配，MA2/2015无可提取文字待人工核验，PMK23/2022首页RANCANGAN与目录正式类型冲突。版本审查子代理将明确证据导入body\_review，冲突PDF保留历史但排除当前原件清单；首两页匹配不是全文、法律效力或最新版本核验。 |
| 去重验证 | 新目录基线0份PDF；首次完成轮新增14份；默认重跑14previous\_success且成功正文0请求，仍重试2个DPR失败原文；刷新重跑14content\_duplicate。14个原始文件的路径/SHA均一致，没有新增、删除或字节变更。 |
| 重试口径 | 默认上限3次（含首次）。原文完成轮17个正文请求事件，单文件最多2次；默认重跑2个失败正文事件；刷新重跑16个正文事件。目录请求状态含URL，但不计作正文请求事件；403不是暂时性错误，因此不重复请求。 |
| 本轮修复 | \["贸易部详情验证正式类型、标题、本法编号/年份，排除摘要/FAQ/草案及/frontend-new/全站应用ZIP。元数据阶段发现该应用ZIP，原文开始前修正过滤器并中止第1轮后按批次继续；应用ZIP未下载。", "中止执行保存明确类型和原因，历史尝试继续保留。", "新增正文身份审查登记及当前原件冲突排除；审核绑定SHA，缓存、重建和同字节刷新保留审核；未来批次报告单列body\_review。"\] |
| 报告验收 | 每轮JSON/Markdown/HTML包含状态、次数、原因；元数据轮、刷新轮及最终汇总HTML已在本地浏览器实际打开并查看排版，固定样式、中文表头和折叠明细可读。 |
| 分工 | 主代理执行真实采集、重跑、报告及skill维护；版本审查子代理检查日期/身份与贸易部过滤并实现正文审查登记；输出审查子代理核对原件、首两页、哈希、状态和报告。 |
| 证据文件 | \["/Users/rpy/home/github/law-crawler/reports/indonesia-version-audit-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/indonesia-output-final-audit-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/indonesia-pdf-identity-audit-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/indonesia-rerun-file-stability-2026-10-14.json"\] |
| 剩余平台访问问题 | peraturan.go.id TLS EOF；BPK、MK、DPR原文403，合计4个平台、5条错误。JDIHN本轮使用官方dev备用部署取得2份原文，正式站诊断保留，备用成功不表示正式站恢复。 |
| 自动检查 | 179项unittest通过；skill结构quick\_validate通过；sync\_skill --check通过；工程skill与本机Codex安装副本一致。 |

## 数量汇总

| 状态 | 数量 |
| --- | --- |
| 目录元数据 | 16 |
| 首次新增PDF | 14 |
| 默认重跑复用 | 14 |
| 刷新内容重复 | 14 |
| 正文首两页身份匹配 | 12 |
| 扫描身份未知 | 1 |
| 目录正文类型冲突 | 1 |
| 当前原件候选 | 13 |
| 当前原件排除 | 1 |

## 法规目录与元数据

| 法规名称 | 目录元数据 | 选择 |
| --- | --- | --- |
| Undang-Undang Nomor 4 Tahun 2026 Tentang PERUBAHAN ATAS UNDANG-UNDANG NOMOR 4 TAHUN 2023 TENTANG PENGEMBANGAN DAN PENGUATAN SEKTOR KEUANGAN | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/2c1ba3d090fcc625d4c7003003379e4e1a81018d5ddc18fd33c3d207d8fc302c.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "2c1ba3d090fcc625d4c7003003379e4e1a81018d5ddc18fd33c3d207d8fc302c"} |
| Undang-Undang Nomor 5 Tahun 2026 Tentang PERUBAHAN KETIGA ATAS UNDANG-UNDANG NOMOR 2 TAHUN 2002 TENTANG KEPOLISIAN NEGARA REPUBLIK INDONESIA | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/7a8306572972e873e40120668a1c9d5d1381d944c8e3bc6e8b3f2cafa5c9b9f2.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "7a8306572972e873e40120668a1c9d5d1381d944c8e3bc6e8b3f2cafa5c9b9f2"} |
| Perma Nomor 2 Tahun 2015 \| Tata Cara Penyelesaian Gugatan Sederhana | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/999b89852111933480543d65e8cb0660be7f0896ce05f283c21af9d82779459c.json | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "999b89852111933480543d65e8cb0660be7f0896ce05f283c21af9d82779459c"} |
| Perma 7 Tahun 2015 \| Organisasi dan Tata Kerja Kepaniteraan dan Kesekretariatan Peradilan | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/ec05265024f67df7f2550453785ef38835832b2b94f2937fdb007088d21ea430.json | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "ec05265024f67df7f2550453785ef38835832b2b94f2937fdb007088d21ea430"} |
| Peraturan Dewan Perwakilan Rakyat Republik Indonesia Nomor 1 Tahun 2025 Tentang Amendment to Regulation of The House of Representatives of The Republic of Indonesia Number 1 of 2020 on Rules of Procedure | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/e918bd009133fba502c8c85fde94b1517b38709bb0a8d479698b9b0eb873f7f0.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "e918bd009133fba502c8c85fde94b1517b38709bb0a8d479698b9b0eb873f7f0"} |
| Peraturan Dewan Perwakilan Rakyat Republik Indonesia Nomor 2 Tahun 2025 Tentang Rencana Strategis Dewan Perwakilan Rakyat Republik Indonesia Tahun 2025 - 2029 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/9c738bc837b30c01e25543914cd3c471397a143d1d0abab640b492a6dd45fa28.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "9c738bc837b30c01e25543914cd3c471397a143d1d0abab640b492a6dd45fa28"} |
| Laporan Berkala Lembaga Penjamin | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/c9c26eb551706606d7537b240ebf9bb89550f81757f754ef8f0e483d8b5e7392.json | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "c9c26eb551706606d7537b240ebf9bb89550f81757f754ef8f0e483d8b5e7392"} |
| Penyelenggaraan Bursa Mineral dan Komoditas Strategis | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/e6b850b6cecfbe6841c8737b73476e1551e58f3e7f0f686d55317719362ad329.json | {"latest\_unverified": true, "reason": "single\_candidate\_date\_unknown", "selected": true, "selected\_id": "e6b850b6cecfbe6841c8737b73476e1551e58f3e7f0f686d55317719362ad329"} |
| Peraturan Menteri Perdagangan Republik Indonesia Nomor 25 Tahun 2026 Tentang Petunjuk Teknis Pengelolaan Konflik Kepentingan Di Lingkungan Kementerian Perdagangan | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/ef5ba12c6f759f922f820fe0212ff98cd35bbcc52b6c67767d3100fa0b134511.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "ef5ba12c6f759f922f820fe0212ff98cd35bbcc52b6c67767d3100fa0b134511"} |
| Peraturan Menteri Perdagangan Republik Indonesia Nomor 24 Tahun 2026 tentang Perubahan Kedua Atas Peraturan Menteri Perdagangan Nomor 08/M-DAG/PER/4/2011 tentang Organisasi dan Tata Kerja Kantor Dagang dan Ekonomi Indonesia di Taipei | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/228fe8616847a56526df5426e3f28eb19631ce3180c062719a108c0498cb454c.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "228fe8616847a56526df5426e3f28eb19631ce3180c062719a108c0498cb454c"} |
| Peraturan Menteri Kesehatan Nomor 6 Tahun 2026 tentang Rumah Sakit | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/b81a8f9d0f256802820a8400811dc766504259a38636a799fdd30aaedf03cb4b.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "b81a8f9d0f256802820a8400811dc766504259a38636a799fdd30aaedf03cb4b"} |
| Peraturan Menteri Kesehatan Nomor 23 Tahun 2022 tentang Penanggulangan Human Immunodeficiency Virus , Acquired Immunodeficiency Syndrome , dan Infeksi Menular Seksual | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/f90cbeb0741002cfddb26197c978da0f88bb3284792394f39b135d77f394ae37.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "f90cbeb0741002cfddb26197c978da0f88bb3284792394f39b135d77f394ae37"} |
| Tata Cara Pelaksanaan Kegiatan Subsidi Bunga/Subsidi Margin Kredit Perempuan Prasejahtera | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/e7cd20b704ad51209942933ebe7e1b7491876cc006f689400b9c79465f131e5a.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "e7cd20b704ad51209942933ebe7e1b7491876cc006f689400b9c79465f131e5a"} |
| Tata Cara Pengalokasian, Pembayaran, dan Pertanggungjawaban Dana Bantuan untuk Kecukupan Dana Jaminan Sosial Kesehatan | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/a254d3c5369c78b94b2f82cdd6a12733f3df16bb192d73e2d302a30563f3eee2.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "a254d3c5369c78b94b2f82cdd6a12733f3df16bb192d73e2d302a30563f3eee2"} |
| Peraturan Menteri Investasi dan Hilirisasi/Kepala Badan Koordinasi Penanaman Modal Nomor 6 Tahun 2026 tentang Tata Cara Pembentukan Peraturan Menteri Investasi dan Hilirisasi/Kepala Badan Koordinasi Penanaman Modal | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/935623c8ea8cad0ab09582668e7acb1ea41f7902aee7ab674da78902c41a3171.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "935623c8ea8cad0ab09582668e7acb1ea41f7902aee7ab674da78902c41a3171"} |
| Peraturan Menteri Investasi dan Hilirisasi/Kepala Badan Koordinasi Penanaman Modal Nomor 5 Tahun 2026 tentang Penilaian Terhadap Kinerja Pelayanan Terpadu Satu Pintu dan Kinerja Percepatan Pelaksanaan Berusaha | /Users/rpy/home/github/law-crawler/downloads/Indonesia/catalog/492199d56e246dea6e267178e04ec0bc2edd70f875849edea6cee36dc4133212.json | {"latest\_unverified": false, "reason": "latest\_published\_at", "selected": true, "selected\_id": "492199d56e246dea6e267178e04ec0bc2edd70f875849edea6cee36dc4133212"} |

## 逐文件执行状态

| 阶段 | 批次 | 轮次 | 结果 | 目录条数 | 文件数 | 处理统计 | 正文请求事件 | 单文件最多尝试 | 错误数 | HTML报告 | Markdown报告 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 元数据 | indonesia-metadata-validation-20261014 | 1 | completed\_with\_errors | 16 | 0 | {} | 0 | 0 | 3 | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-metadata-validation-20261014/attempts/0001/report.html | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-metadata-validation-20261014/attempts/0001/report.md |
| 原文 | indonesia-content-validation-20261014 | 1 | interrupted | 0 | 0 | {} | 0 | 0 | 3 | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0001/report.html | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0001/report.md |
| 原文 | indonesia-content-validation-20261014 | 2 | completed\_with\_errors | 16 | 14 | {"downloaded": 14} | 17 | 2 | 5 | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0002/report.html | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0002/report.md |
| 原文 | indonesia-content-validation-20261014 | 3 | completed\_with\_errors | 16 | 14 | {"previous\_success": 14} | 2 | 1 | 5 | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0003/report.html | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0003/report.md |
| 原文 | indonesia-content-validation-20261014 | 4 | completed\_with\_errors | 16 | 14 | {"content\_duplicate": 14} | 16 | 1 | 5 | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0004/report.html | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-content-validation-20261014/attempts/0004/report.md |

## 下载文件

| 法规名称 | 平台 | 公布日期 | 正文核验状态 | 核验原因 | 原始文件 | 文件元数据 | SHA256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Undang-Undang Nomor 4 Tahun 2026 Tentang PERUBAHAN ATAS UNDANG-UNDANG NOMOR 4 TAHUN 2023 TENTANG PENGEMBANGAN DAN PENGUATAN SEKTOR KEUANGAN | jdihn.go.id | 2026-06-17 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/dab7d3f02b6b4755c4f64eb3859ea6d717559465fc7947b0f10d0cb79a884483.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/dab7d3f02b6b4755c4f64eb3859ea6d717559465fc7947b0f10d0cb79a884483.json | dab7d3f02b6b4755c4f64eb3859ea6d717559465fc7947b0f10d0cb79a884483 |
| Undang-Undang Nomor 5 Tahun 2026 Tentang PERUBAHAN KETIGA ATAS UNDANG-UNDANG NOMOR 2 TAHUN 2002 TENTANG KEPOLISIAN NEGARA REPUBLIK INDONESIA | jdihn.go.id | 2026-06-17 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/689f9ed97f43900c64be3c44f8eef9f91091e3a6a48691943ceb6788a99931d2.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/689f9ed97f43900c64be3c44f8eef9f91091e3a6a48691943ceb6788a99931d2.json | 689f9ed97f43900c64be3c44f8eef9f91091e3a6a48691943ceb6788a99931d2 |
| Perma Nomor 2 Tahun 2015 \| Tata Cara Penyelesaian Gugatan Sederhana | mahkamahagung.go.id |  | unknown\_no\_extractable\_text\_first\_two\_pages | 前两页无可提取文字；不做OCR；只能记录目录身份，未在PDF正文独立核验编号/年份/类型。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/547d9ca31e80fa63b01dd0d3174e394f63527945ae28daff8910e65fc1264689.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/547d9ca31e80fa63b01dd0d3174e394f63527945ae28daff8910e65fc1264689.json | 547d9ca31e80fa63b01dd0d3174e394f63527945ae28daff8910e65fc1264689 |
| Perma 7 Tahun 2015 \| Organisasi dan Tata Kerja Kepaniteraan dan Kesekretariatan Peradilan | mahkamahagung.go.id |  | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/649a98a88c8bb9e55499bc536bcdd5874acee4f12f2219dfe2d3ffd40e51cc43.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/649a98a88c8bb9e55499bc536bcdd5874acee4f12f2219dfe2d3ffd40e51cc43.json | 649a98a88c8bb9e55499bc536bcdd5874acee4f12f2219dfe2d3ffd40e51cc43 |
| Laporan Berkala Lembaga Penjamin | ojk.go.id |  | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/48222f800fe476548501f8f8b5a9e60bc95684e34196dad8e204d049741d5f9c.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/48222f800fe476548501f8f8b5a9e60bc95684e34196dad8e204d049741d5f9c.json | 48222f800fe476548501f8f8b5a9e60bc95684e34196dad8e204d049741d5f9c |
| Penyelenggaraan Bursa Mineral dan Komoditas Strategis | ojk.go.id |  | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/07271816d9864f6b7eca29222b5fcd73d6d37a8132aa5516c0a8eedddbfb0764.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/07271816d9864f6b7eca29222b5fcd73d6d37a8132aa5516c0a8eedddbfb0764.json | 07271816d9864f6b7eca29222b5fcd73d6d37a8132aa5516c0a8eedddbfb0764 |
| Peraturan Menteri Perdagangan Republik Indonesia Nomor 25 Tahun 2026 Tentang Petunjuk Teknis Pengelolaan Konflik Kepentingan Di Lingkungan Kementerian Perdagangan | kemendag.go.id | 2026-10-12 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/52b6bb8ee41bf28cb090614a0050258c1f4555be416ec6b8734415ec94db1baa.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/52b6bb8ee41bf28cb090614a0050258c1f4555be416ec6b8734415ec94db1baa.json | 52b6bb8ee41bf28cb090614a0050258c1f4555be416ec6b8734415ec94db1baa |
| Peraturan Menteri Perdagangan Republik Indonesia Nomor 24 Tahun 2026 tentang Perubahan Kedua Atas Peraturan Menteri Perdagangan Nomor 08/M-DAG/PER/4/2011 tentang Organisasi dan Tata Kerja Kantor Dagang dan Ekonomi Indonesia di Taipei | kemendag.go.id | 2026-09-18 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/f8994734b8cf35cda8bf15ed7f8cc130b5941491a2d8c4d055a5e6075bc0e1ad.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/f8994734b8cf35cda8bf15ed7f8cc130b5941491a2d8c4d055a5e6075bc0e1ad.json | f8994734b8cf35cda8bf15ed7f8cc130b5941491a2d8c4d055a5e6075bc0e1ad |
| Peraturan Menteri Kesehatan Nomor 6 Tahun 2026 tentang Rumah Sakit | kemkes.go.id | 2026-06-12 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/0cd51f332efbc1fdb0e8607d82d1da26d5dc7571c5a16e4e9ea29216fbf64aed.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/0cd51f332efbc1fdb0e8607d82d1da26d5dc7571c5a16e4e9ea29216fbf64aed.json | 0cd51f332efbc1fdb0e8607d82d1da26d5dc7571c5a16e4e9ea29216fbf64aed |
| Peraturan Menteri Kesehatan Nomor 23 Tahun 2022 tentang Penanggulangan Human Immunodeficiency Virus , Acquired Immunodeficiency Syndrome , dan Infeksi Menular Seksual | kemkes.go.id | 2022-09-01 | catalogue\_body\_type\_conflict | 正文第一词为RANCANGAN；目录宣称正式法规，但正文类型存在冲突，不能作为已核验正式法规。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/ab3b0edf0fe45f77bd755bed70552ea8a1a3d3529e7e37e718b94a07f3a3bfa1.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/ab3b0edf0fe45f77bd755bed70552ea8a1a3d3529e7e37e718b94a07f3a3bfa1.json | ab3b0edf0fe45f77bd755bed70552ea8a1a3d3529e7e37e718b94a07f3a3bfa1 |
| Tata Cara Pelaksanaan Kegiatan Subsidi Bunga/Subsidi Margin Kredit Perempuan Prasejahtera | kemenkeu.go.id | 2026-10-08 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/66dc780c12e2a378d45215a0d01de18b30b6cd923ceb597e5dad824099f856b4.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/66dc780c12e2a378d45215a0d01de18b30b6cd923ceb597e5dad824099f856b4.json | 66dc780c12e2a378d45215a0d01de18b30b6cd923ceb597e5dad824099f856b4 |
| Tata Cara Pengalokasian, Pembayaran, dan Pertanggungjawaban Dana Bantuan untuk Kecukupan Dana Jaminan Sosial Kesehatan | kemenkeu.go.id | 2026-10-08 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/b037b9b357ab682c45ae3d53ec82d9710bdd687dea12847ae930deef6a058aec.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/b037b9b357ab682c45ae3d53ec82d9710bdd687dea12847ae930deef6a058aec.json | b037b9b357ab682c45ae3d53ec82d9710bdd687dea12847ae930deef6a058aec |
| Peraturan Menteri Investasi dan Hilirisasi/Kepala Badan Koordinasi Penanaman Modal Nomor 6 Tahun 2026 tentang Tata Cara Pembentukan Peraturan Menteri Investasi dan Hilirisasi/Kepala Badan Koordinasi Penanaman Modal | bkpm.go.id | 2026-07-16 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/f30c5a276be4c7fc4169536ab135b605908929174130c5f9a576d2843d5f4c7e.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/f30c5a276be4c7fc4169536ab135b605908929174130c5f9a576d2843d5f4c7e.json | f30c5a276be4c7fc4169536ab135b605908929174130c5f9a576d2843d5f4c7e |
| Peraturan Menteri Investasi dan Hilirisasi/Kepala Badan Koordinasi Penanaman Modal Nomor 5 Tahun 2026 tentang Penilaian Terhadap Kinerja Pelayanan Terpadu Satu Pintu dan Kinerja Percepatan Pelaksanaan Berusaha | bkpm.go.id | 2026-07-07 | title\_type\_number\_year\_match | 人工核对前两页标题、法规类型、编号/年份与metadata一致；不代表效力或全文核验。 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/0b71537a88b072676a30e1847b011ac66e89b27f818e952c0a7108629cac4b8f.pdf | /Users/rpy/home/github/law-crawler/downloads/Indonesia/0b71537a88b072676a30e1847b011ac66e89b27f818e952c0a7108629cac4b8f.json | 0b71537a88b072676a30e1847b011ac66e89b27f818e952c0a7108629cac4b8f |

## 错误与原因

| 官方平台 | 原件来源 | 失败原因 | 国家 | 平台 |
| --- | --- | --- | --- | --- |
| peraturan.go.id | https://peraturan.go.id/ | 官方入口访问失败；https://peraturan.go.id/: &lt;urlopen error \[SSL: UNEXPECTED\_EOF\_WHILE\_READING\] EOF occurred in violation of protocol (\_ssl.c:1082)&gt;；https://www.peraturan.go.id/: &lt;urlopen error \[SSL: UNEXPECTED\_EOF\_WHILE\_READING\] EOF occurred in violation of protocol (\_ssl.c:1082)&gt; |  |  |
| bpk.go.id | https://peraturan.bpk.go.id/ | 官方入口访问失败；https://peraturan.bpk.go.id/: HTTP Error 403: Forbidden |  |  |
| mkri.id | https://www.mkri.id/peraturan/peraturan-mk | HTTP Error 403: Forbidden |  |  |
| dpr.go.id | https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr001.pdf?t=20261014213917 | HTTP Error 403: Forbidden | Indonesia |  |
| dpr.go.id | https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr002.pdf?t=20261014213920 | HTTP Error 403: Forbidden | Indonesia |  |
|  |  | PMK23/2022：目录正文类型冲突，原件仅作审计证据，未完成正式原件核验 |  | kemkes.go.id |
|  |  | PERMA2/2015：首两页无可提取文字，身份未核验 |  | mahkamahagung.go.id |

## 平台结果

| 平台 | 下载状态 | 文件数 | 访问错误 | 正式入口诊断 |
| --- | --- | --- | --- | --- |
| peraturan.go.id | content\_failed | 0 | \[{"error": "官方入口访问失败；https://peraturan.go.id/: &lt;urlopen error \[SSL: UNEXPECTED\_EOF\_WHILE\_READING\] EOF occurred in violation of protocol (\_ssl.c:1082)&gt;；https://www.peraturan.go.id/: &lt;urlopen error \[SSL: UNEXPECTED\_EOF\_WHILE\_READING\] EOF occurred in violation of protocol (\_ssl.c:1082)&gt;", "platform": "peraturan.go.id", "url": "https://peraturan.go.id/"}\] |  |
| bpk.go.id | content\_failed | 0 | \[{"error": "官方入口访问失败；https://peraturan.bpk.go.id/: HTTP Error 403: Forbidden", "platform": "bpk.go.id", "url": "https://peraturan.bpk.go.id/"}\] |  |
| mkri.id | content\_failed | 0 | \[{"error": "HTTP Error 403: Forbidden", "platform": "mkri.id", "url": "https://www.mkri.id/peraturan/peraturan-mk"}\] |  |
| jdihn.go.id | experimental\_success | 2 | \[\] | {"entries": \["https://jdihn.go.id/pencarian", "https://jdihn.go.id/"\], "error": "官方入口访问失败；https://jdihn.go.id/pencarian: &lt;urlopen error \[SSL: UNEXPECTED\_EOF\_WHILE\_READING\] EOF occurred in violation of protocol (\_ssl.c:1082)&gt;；https://jdihn.go.id/: &lt;urlopen error \[SSL: UNEXPECTED\_EOF\_WHILE\_READING\] EOF occurred in violation of protocol (\_ssl.c:1082)&gt;", "status": "failed"} |
| mahkamahagung.go.id | success | 2 | \[\] |  |
| dpr.go.id | content\_failed | 0 | \[{"country": "Indonesia", "error": "HTTP Error 403: Forbidden", "platform": "dpr.go.id", "url": "https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr001.pdf?t=20261014213917"}, {"country": "Indonesia", "error": "HTTP Error 403: Forbidden", "platform": "dpr.go.id", "url": "https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr002.pdf?t=20261014213920"}\] |  |
| ojk.go.id | success | 2 | \[\] |  |
| kemendag.go.id | success | 2 | \[\] |  |
| kemkes.go.id | success | 2 | \[\] |  |
| kemenkeu.go.id | success | 2 | \[\] |  |
| bkpm.go.id | success | 2 | \[\] |  |

