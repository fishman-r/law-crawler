# 印尼受阻平台恢复与正文补核 · 2026-10-14

> 本报告记录本次执行结果；下载成功不代表法律效力已核验。

## 执行信息

| 项目 | 值 |
| --- | --- |
| 适用国家/法域 | Indonesia |
| 采集模式 | recovery\_verification |
| 保存目录 | /Users/rpy/home/github/law-crawler/downloads/Indonesia |
| 结论 | MK已走通正常浏览器公开目录→CLI公开原件→完整格式/正文身份核验→配置目录保存。其CLI自动目录访问仍未恢复；peraturan、BPK、DPR当前原件仍受阻。 |
| 核验更正 | PMK23/2022提取词RANCANGAN是白色隐藏文字；真实渲染、制定语句、公布/认证页支持正式身份。此前单凭提取判草案冲突有误。本次保留首次审计、原PDF及body\_review\_history，按新证据更正并恢复当前候选。PERMA2/2015扫描首页与末页视觉匹配，不依赖OCR文本。 |
| 日期 | PERMA2/2015实际PDF制定及公布均2015-08-07，已补文件元数据；PMK23/2022制定2022-08-31/公布2022-09-01。MK两份制定日分别2026-09-16、2025-08-20；未观察到公布日期，published\_at仍null。 |
| 最新版本边界 | 本次身份核验不判断全文完整性、附件逐字完整、当前法律效力或全站最新。当前16文件中11份公布日已知、5份未知；未知保留latest\_unverified，不用制定日或下载日替代公布日。 |
| 大文件 | PMK1/2026完整74,168,413字节；PMK7/2025完整119,790,543字节。默认40MiB初探会截断拒收，只有完整PDF原字节进入配置目录。本次明确128MiB导入上限；CLI传输维持TLS验证、有界超时及最多3次，离线导入不发网络请求。 |
| 去重 | 新增2个SHA文件，先前14份原始字节哈希均未改变。MK导入3轮分别downloaded2/existing2/content\_duplicate2，始终16个PDF，审核及PDF观察日期在重跑后保留。 |
| 备用入口 | DJPP官网可读，但法规导航仍指向TLS失败站，KUHAP附件为草案/审议材料。BPK本机关JDIH范围更窄且本机仍403。DPR仓库2020年汇编可达但不替代2025正式原件，未混入法规原件库存。 |
| 浏览器边界 | MK目录实际可读；大PDF下载未返回本地路径，后续读取工具超时。BPK、DPR及peraturan浏览器尝试也为工具超时，不能当成网站拒绝。两份新增原件标记official\_cli\_download，目录来源标browser\_catalogue\_cli\_original。 |
| 批次 | \["indonesia-recovery-followup-20261014", "indonesia-recovery-body-20261014", "indonesia-mk-official-files-20261014", "indonesia-mk-body-review-20261014"\] |
| 分工 | 主代理：浏览器、MK1传输、导入/重跑、报告及skill。网络调试子代理：官方入口、MK7完整原件及诊断。正文核验子代理：PDF渲染、隐藏文字分析、正式身份和日期。持久化子代理：审核历史/元数据证据保护及回归测试。 |
| 自动检查 | 196项unittest通过；sync\_skill --check和quick\_validate通过；工程skill与本机安装副本已同步。新报告由既有固定HTML模板生成；本轮浏览器后续读取超时，未把静态结构检查冒充新HTML渲染验收。 |
| 证据文件 | \["/Users/rpy/home/github/law-crawler/reports/network-routes-followup-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/indonesia-browser-followup-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/body-verification-followup-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/indonesia-pdf-identity-followup-audit-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/indonesia-mk-body-review-followup-2026-10-14.json"\] |

## 数量汇总

| 状态 | 数量 |
| --- | --- |
| 新增MK原件 | 2 |
| 默认重跑复用 | 2 |
| 全项重跑内容重复 | 2 |
| 两份既有异常补核 | 2 |
| 当前PDF原件 | 16 |
| 身份核验匹配 | 16 |
| 当前排除记录 | 0 |
| 仍受阻平台 | 3 |

## 法规目录与元数据

| 本地文件 | 状态 | 原因 | 核验证据 |
| --- | --- | --- | --- |
| /Users/rpy/home/github/law-crawler/downloads/Indonesia/ab3b0edf0fe45f77bd755bed70552ea8a1a3d3529e7e37e718b94a07f3a3bfa1.pdf | title\_type\_number\_year\_match | 真实渲染第1页显示正式Peraturan Menteri Kesehatan 23/2022标题及主题；第3页MEMUTUSKAN/Menetapkan与正文一致，第31页完整制定、公布、BN2022/831及Salinan认证章，第32页附件身份和第119页签署认证一致。首次提取的RANCANGAN为第1页白色隐藏文字（1 g），随后标题恢复黑色（0 g），仅提取层可见；综合证据支持隐藏模板残留解释，不能单凭该词判为草案，也不是单凭签名消除冲突。保留该词原字节与首次冲突审计。本次只确认正文身份及列明页面，不确认全文逐字完整、法律效力或最新版本。 | \["reports/evidence/indonesia-body-followup/pmk-first.png", "reports/evidence/indonesia-body-followup/pmk-enacting.png", "reports/evidence/indonesia-body-followup/pmk-signature.png", "reports/evidence/indonesia-body-followup/pmk-attachment-first.png", "reports/evidence/indonesia-body-followup/pmk-last.png"\] |
| /Users/rpy/home/github/law-crawler/downloads/Indonesia/547d9ca31e80fa63b01dd0d3174e394f63527945ae28daff8910e65fc1264689.pdf | title\_type\_number\_year\_match | 该PDF为扫描件，文字提取无文字不等于身份未知。人工真实渲染第1页明确显示PERATURAN MAHKAMAH AGUNG REPUBLIK INDONESIA NOMOR 2 TAHUN 2015及TATA CARA PENYELESAIAN GUGATAN SEDERHANA；第2页为对应序言延续，第14页Ditetapkan 7 Agustus 2015/MUHAMMAD HATTA ALI，第15页Diundangkan 7 Agustus 2015/YASONNA H. LAOLY、BN2015/1172及认证章。身份与metadata匹配；本次未做OCR、不确认全文逐字完整、法律效力或最新版本。 | \["reports/evidence/indonesia-body-followup/perma-first.png", "reports/evidence/indonesia-body-followup/perma-second.png", "reports/evidence/indonesia-body-followup/perma-penultimate.png", "reports/evidence/indonesia-body-followup/perma-last.png"\] |

## 逐文件执行状态

| phase | attempt | counts | network\_requests | acquisition\_method | report |
| --- | --- | --- | --- | --- | --- |
| MK配置目录离线导入 | 1 | {"downloaded": 2} | 0 | official\_cli\_download | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-mk-official-files-20261014/attempts/0001/report.html |
| MK配置目录离线导入 | 2 | {"existing": 2} | 0 | official\_cli\_download | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-mk-official-files-20261014/attempts/0002/report.html |
| MK配置目录离线导入 | 3 | {"content\_duplicate": 2} | 0 | official\_cli\_download | /Users/rpy/home/github/law-crawler/downloads/.batches/indonesia-mk-official-files-20261014/attempts/0003/report.html |

## 下载文件

| 本地文件 | 内容SHA-256 | size\_bytes | 法规名称 | 元数据文件 | 正文身份核验 | published\_at |
| --- | --- | --- | --- | --- | --- | --- |
| /Users/rpy/home/github/law-crawler/downloads/Indonesia/b400e7e8ecb1494aa98ecb21fc771eff76067a396fe122b749bb2d686b7f3a57.pdf | b400e7e8ecb1494aa98ecb21fc771eff76067a396fe122b749bb2d686b7f3a57 | 74168413 | Peraturan Mahkamah Konstitusi Nomor 1 Tahun 2026 tentang Tahapan, Kegiatan, dan Jadwal Penanganan Permohonan Mengenai Perselisihan Hasil Pemilihan Umum Presiden dan Wakil Presiden Tahun 2024 yang Diajukan Pada Tahun 2026 | /Users/rpy/home/github/law-crawler/downloads/Indonesia/b400e7e8ecb1494aa98ecb21fc771eff76067a396fe122b749bb2d686b7f3a57.json | title\_type\_number\_year\_match |  |
| /Users/rpy/home/github/law-crawler/downloads/Indonesia/d0a96228b5de6228d388731fc4070fd10e2774b2ea43f2c7bb23b8d1f436bb1b.pdf | d0a96228b5de6228d388731fc4070fd10e2774b2ea43f2c7bb23b8d1f436bb1b | 119790543 | Peraturan Mahkamah Konstitusi Nomor 7 Tahun 2025 tentang Tata Beracara dalam Perkara Pengujian Undang-Undang | /Users/rpy/home/github/law-crawler/downloads/Indonesia/d0a96228b5de6228d388731fc4070fd10e2774b2ea43f2c7bb23b8d1f436bb1b.json | title\_type\_number\_year\_match |  |

## 错误与原因

| 官方平台 | 失败原因 |
| --- | --- |
| peraturan.go.id | TLS EOF；TLS1.2握手/单次直连对照也未完成 |
| bpk.go.id | 综合库及BPK本机关JDIH目录/详情/原件HTTP403 |
| dpr.go.id | 目录详情200，2025年两份正文在berkas主机Akamai403 |

## 平台结果

| 官方平台 | 执行结果 | 原因 | alternate | file\_count | cli\_catalogue\_recovered |
| --- | --- | --- | --- | --- | --- |
| peraturan.go.id | blocked | urllib TLS EOF on main/www and original file; verified TLS1.2 curl handshake timeout; one explicit-proxy-bypass curl TCP timeout. This does not identify whether remote endpoint or upstream route is responsible. | HTML 200; actual regulation navigation points back to peraturan.go.id. /kuhap HTML 200 only advertises draft and committee materials, not verified formal originals. |  |  |
| bpk.go.id | blocked | Actual original download blocked; readable search service HTML cannot establish local recovery. | Web search reader sees live index/detail and official download link; local CLI index/detail/download all 403. No original bytes downloaded or verified. |  |  |
| dpr.go.id | original\_download\_blocked |  | Actual original-byte PDF download 200, but this is an official 2020 legislative compilation, excluded from current-version route recovery and persistent intake. |  |  |
| mkri.id | browser\_catalogue\_cli\_originals\_verified |  |  | 2 | False |

