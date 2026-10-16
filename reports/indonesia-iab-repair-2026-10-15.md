# IAB读取恢复边界与印尼三平台官方入口验证 · 2026-10-15

> 本报告记录本次执行结果；下载成功不代表法律效力已核验。

## 执行信息

| 项目 | 值 |
| --- | --- |
| 适用国家/法域 | Indonesia |
| 采集模式 | recovery\_verification |
| 执行结果 | iab\_bounded\_read\_and\_attachment\_passed\_originals\_blocked |
| 保存目录 | /Users/rpy/home/github/law-crawler/downloads/Indonesia |
| IAB结果 | 官网标准快照和DOM读取已通过；50秒外层时限下后台简短DOM约30秒。可见状态截图通过，已恢复隐藏。短外层8—12秒曾导致工具重置，内部timeoutMs不替代传输时限；宿主根因未知，不宣称浏览器内部代码修复。 |
| 下载诊断 | 工程skill新增标准库loopback固定内存健康服务；明确附件响应实际下载128字节、SHA匹配。普通download属性TXT未返回事件。合成附件不是法规，不计入库存。 |
| 官网结果 | Chrome新BPK Kalbar/Yogyakarta/Sumut正式发布及原件均拒绝；DPR3/2025正式详情可读，正常点击原件仍Akamai拒绝；DJPP e-Pengundangan入口连接关闭。Sumut此前受Mac锁定中断，解锁后已补核，原文新增0。 |
| 元数据与去重 | 现有schema2、URL/队列/持久状态/SHA去重、原字节格式及批次逻辑保留。既有16份PDF及每文件JSON哈希/大小匹配且未变化。未取得合格新原件，不创建成功文件元数据、不执行空导入重跑。HTML详情和搜索读到的PDF不能替代本地原文。 |
| 最新与正文边界 | DPR3/2025详情公布日期2025-11-07只作为该来源详情证据；未取得PDF，未确认原文。Yogyakarta166页候选搜索文本含正式PBPK3/2025与认证，区别于旧草案，但没有本地正文核验；不扩展适配器主机白名单。 |
| Markdown转换 | 本轮没有新HTML/XML/JSON法规正文，因此在线正文Markdown转换不适用；两种执行报告已生成，转换/去重/批次回归通过。 |
| 工程skill | 独立browser\_healthcheck.py源码在工程skill中；更新SKILL.md、browser-workflow.md、network-troubleshooting.md和README；已同步本机安装副本。 |
| 验证 | 205项unittest通过，quick\_validate通过，sync\_skill --check通过；16份原件字节和元数据匹配。 |
| 分工 | 主代理：IAB与Chrome实际验证、库存、报告、skill整合；fresh\_official\_originals网络子代理：8次有界官方路线请求；browser\_health\_fixture编写测试子代理：便携诊断服务和9项测试。 |
| 宿主中断 | Mac锁定中断已解决。用户确认解锁后补核Sumut目录及附件，逐个核对关闭7个任务Chrome标签，用户原有6个标签保留；IAB清单为空，任务标签已自动清理。两诊断服务已停止。 |
| 证据文件 | \["/Users/rpy/home/github/law-crawler/reports/browser-timeout-layer-audit-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/chrome-official-originals-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/fresh-official-originals-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/three-platform-inventory-check-2026-10-15.json", "/Users/rpy/home/github/law-crawler/reports/browser-healthcheck-2026-10-15-run1/download-verification.json"\] |
| 批次编号 | indonesia-iab-repair-20261015 |
| 解锁续跑时间 | 2026-10-15T00:05:06.805559+00:00 |
| 执行轮次 | 4 |
| 开始时间（UTC） | 2026-10-15T00:05:06.808176+00:00 |
| 结束时间（UTC） | 2026-10-15T00:05:06.814519+00:00 |

## 数量汇总

| 状态 | 数量 |
| --- | --- |
| 新增法规原文 | 0 |
| 新成功文件元数据 | 0 |
| 现有完整PDF | 16 |
| 既有身份匹配 | 16 |
| CLI新路线请求 | 8 |
| CLI新路线原件403 | 4 |
| Chrome已确认观察 | 10 |
| Chrome待观察 | 0 |
| 诊断附件下载 | 1 |
| 回归测试 | 205 |
| 原文未恢复平台 | 3 |

## 法规目录与元数据

| 官方平台 | 详情来源 | 法规名称 | number | year | published\_at | date\_evidence | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dpr.go.id | https://jdih.dpr.go.id/setjen/detail-dokumen/tipe/peraturan\_dpr/perubahan-atas-peraturan-dewan-perwakilan-rakyat-republik-indonesia-nomor-2-tahun-2015-tentang-tata-beracara-mahkamah-kehormatan-dewan-perwakilan-rakyat-republik-indonesia | Perubahan atas Peraturan DPR Nomor 2 Tahun 2015 tentang Tata Beracara Mahkamah Kehormatan DPR | 3 | 2025 | 2025-11-07 | official detail Tanggal Pengundangan; original unavailable | detail\_observed\_original\_blocked |

## 逐文件执行状态

| 任务标识 | 核验证据 | inherited\_from\_attempt | requests\_this\_continuation | 状态 | 原因 | 请求次数 | 记录时间（UTC） | synthetic | 原件来源 | 重试明细 | request\_kind | observation\_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| iab-read | /Users/rpy/home/github/law-crawler/reports/browser-timeout-layer-audit-2026-10-15.json | 2 | 0 | success | 真实官网后台DOM、标准快照及可见截图通过；宿主根因未知 | 0 | 2026-10-15T00:05:06.808615+00:00 |  |  |  |  |  |
| iab-explicit-diagnostic-download |  | 2 | 0 | success | 合成附件128字节及SHA匹配，不是法规、不入库 | 1 | 2026-10-15T00:05:06.808784+00:00 | True |  |  |  |  |
| cli-route-1 |  | 2 | 0 | fail | HTTP Error 403: Forbidden | 1 | 2026-10-15T00:05:06.809109+00:00 |  | https://kalbar.bpk.go.id/spkn-2026/ | \[{"attempt": 1, "reason": "HTTP Error 403: Forbidden", "status": 403, "tls\_verified": true}\] |  |  |
| cli-route-2 |  | 2 | 0 | fail | HTTP Error 403: Forbidden | 1 | 2026-10-15T00:05:06.809384+00:00 |  | https://kalbar.bpk.go.id/wp-content/uploads/2026/08/SPKN-2026-publikasi.pdf | \[{"attempt": 1, "reason": "HTTP Error 403: Forbidden", "status": 403, "tls\_verified": true}\] |  |  |
| cli-route-3 |  | 2 | 0 | fail | HTTP Error 403: Forbidden | 1 | 2026-10-15T00:05:06.809658+00:00 |  | https://yogyakarta.bpk.go.id/tentang-perwakilan/rencana-strategis-bpk-ri-2006-2010/ | \[{"attempt": 1, "reason": "HTTP Error 403: Forbidden", "status": 403, "tls\_verified": true}\] |  |  |
| cli-route-4 |  | 2 | 0 | fail | HTTP Error 403: Forbidden | 1 | 2026-10-15T00:05:06.810318+00:00 |  | https://yogyakarta.bpk.go.id/wp-content/uploads/2025/12/Renstra-2025-2029.pdf | \[{"attempt": 1, "reason": "HTTP Error 403: Forbidden", "status": 403, "tls\_verified": true}\] |  |  |
| cli-route-5 |  | 2 | 0 | fail | HTTP Error 403: Forbidden | 1 | 2026-10-15T00:05:06.810520+00:00 |  | https://kaltara.bpk.go.id/dasar-hukum-badan-pemeriksa-keuangan-ri/ | \[{"attempt": 1, "reason": "HTTP Error 403: Forbidden", "status": 403, "tls\_verified": true}\] |  |  |
| cli-route-6 |  | 2 | 0 | fail | HTTP Error 403: Forbidden | 1 | 2026-10-15T00:05:06.810725+00:00 |  | https://sumut.bpk.go.id/wp-content/uploads/2026/08/SPKN-2026-publikasi.pdf | \[{"attempt": 1, "reason": "HTTP Error 403: Forbidden", "status": 403, "tls\_verified": true}\] |  |  |
| cli-route-7 |  | 2 | 0 | success | 官方详情HTML取得，不能算原文 | 1 | 2026-10-15T00:05:06.811058+00:00 |  | https://jdih.dpr.go.id/setjen/detail-dokumen/tipe/peraturan\_dpr/perubahan-atas-peraturan-dewan-perwakilan-rakyat-republik-indonesia-nomor-2-tahun-2015-tentang-tata-beracara-mahkamah-kehormatan-dewan-perwakilan-rakyat-republik-indonesia | \[{"attempt": 1, "reason": "HTML response complete", "status": 200, "tls\_verified": true}\] |  |  |
| cli-route-8 |  | 2 | 0 | fail | HTTP Error 403: Forbidden | 1 | 2026-10-15T00:05:06.811301+00:00 |  | https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr003.pdf?t=20260913150826 | \[{"attempt": 1, "reason": "HTTP Error 403: Forbidden", "status": 403, "tls\_verified": true}\] |  |  |
| chrome-route-1 |  | 2 | 0 | fail | 真实Chrome显示BPK-RI拒绝 | 1 | 2026-10-15T00:05:06.811542+00:00 |  | https://kalbar.bpk.go.id/spkn-2026/ |  | detail | access\_denied |
| chrome-route-2 |  | 2 | 0 | fail | 直接打开已观察到的官方附件仍拒绝，未取得文件 | 1 | 2026-10-15T00:05:06.811793+00:00 |  | https://kalbar.bpk.go.id/wp-content/uploads/2026/08/SPKN-2026-publikasi.pdf |  | original | access\_denied |
| chrome-route-3 |  | 2 | 0 | fail | 当前官方Renstra入口仍拒绝 | 1 | 2026-10-15T00:05:06.812049+00:00 |  | https://yogyakarta.bpk.go.id/tentang-perwakilan/rencana-strategis-bpk-ri-2006-2010/ |  | detail | access\_denied |
| chrome-route-4 |  | 2 | 0 | fail | 当前正式候选原件拒绝，搜索解析不算本地文件 | 1 | 2026-10-15T00:05:06.812269+00:00 |  | https://yogyakarta.bpk.go.id/wp-content/uploads/2025/12/Renstra-2025-2029.pdf |  | original | access\_denied |
| chrome-route-5 |  | 2 | 0 | success | 正式印尼语PerDPR3/2025详情可读，编号/类型/制定及公布日期可见 | 1 | 2026-10-15T00:05:06.812494+00:00 |  | https://jdih.dpr.go.id/setjen/detail-dokumen/tipe/peraturan\_dpr/perubahan-atas-peraturan-dewan-perwakilan-rakyat-republik-indonesia-nomor-2-tahun-2015-tentang-tata-beracara-mahkamah-kehormatan-dewan-perwakilan-rakyat-republik-indonesia |  | detail | success |
| chrome-route-6 |  | 2 | 0 | fail | 正常点击UNDUH BERKAS后新标签显示Akamai Access Denied | 1 | 2026-10-15T00:05:06.812733+00:00 |  | https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr003.pdf?t=20261014233608 |  | original | access\_denied |
| chrome-route-7 |  | 2 | 0 | success | 真实DJPP官网导航可读；从可见e-Pengundangan链接正常点击 | 1 | 2026-10-15T00:05:06.812979+00:00 |  | https://djpp.kemenkum.go.id/index.php |  | navigation | success |
| chrome-route-8 |  | 2 | 0 | fail | 真实Chrome显示ERR\_CONNECTION\_CLOSED；不冒充已经到达原件 | 1 | 2026-10-15T00:05:06.813204+00:00 |  | https://e-pengundangan.peraturan.go.id/ |  | navigation | connection\_closed |
| new-original-import |  | 2 | 0 | skip | 没有实际新原件，保持库存和现有元数据、去重；没有空导入或伪造成功 | 0 | 2026-10-15T00:05:06.813469+00:00 |  |  |  |  |  |
| chrome-route-9 |  |  | 0 | fail | 解锁后确认Sumut目录实际Access Denied，原件未取得；前轮中断保留 | 1 | 2026-10-15T00:05:06.813691+00:00 |  | https://sumut.bpk.go.id/publikasi/standar-pemeriksaan-keuangan-negara/ |  | detail | access\_denied |
| chrome-route-10 |  |  | 1 | fail | 解锁后Sumut实际官方附件仍Access Denied，未取得文件；拒绝不重复重试 | 1 | 2026-10-15T00:05:06.813905+00:00 |  | https://sumut.bpk.go.id/wp-content/uploads/2026/08/SPKN-2026-publikasi.pdf |  | original | access\_denied |
| host-cleanup |  |  |  | success | 用户解锁后核对清理7个Chrome任务标签，保留原有标签；IAB空清单已验证，两服务停止 | 0 | 2026-10-15T00:05:06.814112+00:00 |  |  |  |  |  |

## 下载文件

无记录。

## 错误与原因

| 官方平台 | 原件来源 | 原因 | 状态 |
| --- | --- | --- | --- |
| bpk.go.id | https://kalbar.bpk.go.id/spkn-2026/ | 真实Chrome显示BPK-RI拒绝 | access\_denied |
| bpk.go.id | https://kalbar.bpk.go.id/wp-content/uploads/2026/08/SPKN-2026-publikasi.pdf | 直接打开已观察到的官方附件仍拒绝，未取得文件 | access\_denied |
| bpk.go.id | https://yogyakarta.bpk.go.id/tentang-perwakilan/rencana-strategis-bpk-ri-2006-2010/ | 当前官方Renstra入口仍拒绝 | access\_denied |
| bpk.go.id | https://yogyakarta.bpk.go.id/wp-content/uploads/2025/12/Renstra-2025-2029.pdf | 当前正式候选原件拒绝，搜索解析不算本地文件 | access\_denied |
| dpr.go.id | https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr003.pdf?t=20261014233608 | 正常点击UNDUH BERKAS后新标签显示Akamai Access Denied | access\_denied |
| peraturan.go.id | https://e-pengundangan.peraturan.go.id/ | 真实Chrome显示ERR\_CONNECTION\_CLOSED；不冒充已经到达原件 | connection\_closed |
| bpk.go.id | https://sumut.bpk.go.id/publikasi/standar-pemeriksaan-keuangan-negara/ | 用户解锁后真实Chrome显示BPK Access Denied；此前导航后的Mac锁定中断保留在历史轮次 | access\_denied |
| bpk.go.id | https://sumut.bpk.go.id/wp-content/uploads/2026/08/SPKN-2026-publikasi.pdf | 解锁后直接打开已观察的官方附件，Chrome显示BPK拒绝，未取得原字节 | access\_denied |

## 平台结果

| 官方平台 | 状态 | 原因 |
| --- | --- | --- |
| peraturan.go.id | originals\_not\_recovered | DJPP官方导航可读，e-Pengundangan连接关闭，无独立正式原件链 |
| bpk.go.id | originals\_not\_recovered | 新地区正式发布路线CLI403，Chrome Kalbar/Yogyakarta/Sumut目录和原件均拒绝 |
| dpr.go.id | originals\_not\_recovered | PerDPR3/2025详情可读，原件正常点击仍拒绝 |

