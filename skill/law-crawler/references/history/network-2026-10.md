> 保留2026年10月特定环境的历史观察；不将API示例、库存、网络状态或工具超时作为新环境默认事实。文中工程reports路径只用于可选追溯，独立技能包不包含这些报告。执行时回到当前references流程。

## 2026-10-13 的已知边界

本机 `peraturan.go.id` / `jdihn.go.id` 分别解析为 `103.145.96.87` / `103.145.96.88`，无 AAAA。正常 HTTP 代理返回 CONNECT 200 后 TLS 超时；TLS 1.2 和 SOCKS 对照同样失败，绕过显式代理的单次请求 TCP 超时。卫生部对照站能连接。此结果尚不能区分远端故障与上游网络限制。

BPK 和 MK 的拒绝发生在发现入口；DPR 的列表/详情曾成功，同一轮浏览器 UA 请求也出现拒绝，原下载器仍到达正文阶段但 PDF 403。浏览器工具本轮三次连接超时，未取得浏览器页面。

详细逐项证据维护在工程 `data/reports/network-probe-*-2026-10-13.json` 与 `data/reports/remaining-five-retry-2026-10-13.json`。这些是历史诊断，后续执行以新的实际响应为准。

## 2026-10-14 更新

浏览器故障已分层：Chrome 扩展请求仍超时，内置浏览器 IAB 已实际读取 DPR/MK 目录并下载 MK PMK 1/2026。历史的“浏览器工具不可用”不能继续解释所有通道。新增浏览器导入器复用既有元数据和去重；具体步骤及其余四站阻断见 [browser-workflow.md](../browser-workflow.md)。

## 同日后续四站复核与格式更新

继续检查 DJPP/e-pengundangan/BPHN、BPK E-PPID/地区站、DPR PPID/图书馆及真实附件，仍未取得新正式原件。DJPP 可读附件为草案/审议资料，未入库；本机 TLS/403 持续，浏览器随后也出现连接超时。现有原件仍为 10 份 PDF，剩余 4 平台未通过，不能把搜索服务解析视为本地文件下载。

采集和浏览器导入现已共用多格式识别与去重，支持范围和离线/在线验收区别见 [file-formats.md](../file-formats.md)。工程完整记录为 `data/reports/four-platforms-formats-2026-10-14.md`。

## 2026-10-14 五平台再次排查：可选 JDIHN 备用链路

正式 `jdihn.go.id` 仍 TLS EOF。公开的 BPHN dev 部署可读取目录 `/`、详情 `/doc/<id>`，其公开 JSON 明确链接至 Setneg 官方成员原件。新增显式开关，默认流程不使用开发部署：

```sh
python3 "$skill_dir/scripts/download_regulations.py" \
  --root "$work_root" --jurisdiction Indonesia --mode content --index countries/Indonesia/index.md \
  --platform jdihn.go.id --experimental-sources --limit 1 --max-details 1
```

只解析公开响应中的 JSON，不执行脚本或处理挑战令牌。备用主机限定 `dev-jdihn.bphn.go.id`；附件限定已实际验证的 `jdih.setneg.go.id/api/hukumproduk/pdf`。保留公开证据、来源标记 `experimental_official_fallback` 和正式入口诊断 `production_discovery`；成功状态为 `experimental_success`，不是正式站恢复。开发部署可能变动，目录内容不构成法律效力认证。新增成员需实际核实后再扩展白名单。

本轮打包 CLI 从动态目录取得 UU 4/2026（207 页，13,974,767 字节），保存到 `downloads/Indonesia/`；再次运行命中 `existing`。另已验证 UU 1/2026 链路，但该临时探测文件不计入配置目录新增量。

MK 官方档案域 `arsipds.mkri.id` 新增到严格白名单，补收 PMK 7/2025（754,699 字节）并验证再次调用 `existing`。未验证目录到此文件的链接关系，保留 `detail_url: null`、`catalogue_to_file_verified: false`；此直链不作为自动发现兜底。

peraturan 主域及 www 仍 TLS EOF；BPK 原件 `/Read/` 仍 403；DPR 目录/详情可读但正文服务器仍 403。IAB 本轮显示 MK 验证页，DPR 目录可读，但点击后工具超时，不能据此宣称浏览器下载成功。详细证据见工程 `data/reports/five-platforms-recovery-2026-10-14.md`。历史“10 份原件”为旧目录快照，不能当作国家目录当前计数。

## 同日后续：MK公开大原件与正文核验更正

正常IAB已实际读到MK法规目录，`s.mkri.id`公开文件可用正常CLI取得完整PDF：PMK1/2026为74,168,413字节，PMK7/2025为119,790,543字节。默认40MiB不足，截断响应必须拒收；本次明确128MiB导入上限。目录浏览器辅助与CLI取件分别标记，不能将浏览器下载超时算成功或声称CLI自动目录403已恢复。采用 `import_browser_download.py --acquisition-method official_cli_download` 保存到国家目录；默认重跑existing2，全项重跑content_duplicate2。

其余三站仍有真实阻断：peraturan主域/www/原件TLS EOF，TLS1.2握手及单次直连对照未通过；DJPP新官网可读但法规导航回到失败主机，KUHAP附件是草案/审议资料。BPK综合库及本机关JDIH仍403。本机关JDIH比综合库范围窄，不能当等价全面替代。DPR目录、2025详情200，正文Akamai403；官方仓库可达的2020汇编不替代2025原件。后续浏览器工具读取超时与这些CLI网络响应分别记录。

同日三站再核对：原生Chrome替代通道实际可读/截图，但目标页面仍拒绝或断开。BPK成员导航可溯源Riau的`/spkn-2026/`与SPKN2026 PDF，E-PPID页面也提供标为Salinan的PBPK1/2026；本机CLI和原生Chrome所到入口均拒绝，未取得原字节。DPR数字详情`/setjen/detail-dokumen/tipe/peraturan_dpr/id/56`正常浏览器可读，但正常点击UNDUH BERKAS仍到Akamai拒绝页。该印尼语详情公布日25 February 2025与旧英语详情26 February 2025冲突，须保留逐来源差异并等待正式原件确认，不捏造一致日期。DPR旧PPID导航HTTP200但页面内容404，不能记为可用目录。没有新正式下载源时保留失败，避免重跑同一诊断矩阵。

正文复核必须比较提取层与渲染：PMK23/2022的RANCANGAN为白色隐藏文字，真实正式正文、公布和认证页支持身份；PERMA2/2015扫描页可视觉确认。首次冲突/未知判断已按证据更正并保留审核历史，不修改原件。完整实测及剩余条件见工程 `data/reports/indonesia-recovery-followup-2026-10-14.md` / `.html`；当时库存16份PDF、身份匹配16份，不代表法律效力或全站最新认证。

## 2026-10-15：读取超时恢复边界与新官方附件链

IAB本地诊断读取可用；官网短外层调用仍失败，40—50秒有界调用已成功读取真实官网，隐藏状态下简短DOM耗时约30秒。只调整采集操作时限及接口选择，未修改浏览器内部实现；宿主根因未确定。显式附件诊断实际下载128字节并SHA匹配，普通download属性TXT链接未返回事件。新增 `scripts/browser_healthcheck.py` 可重现分层对照，详见浏览器工作流；诊断附件不计法规原文。

网络子代理确认Kalbar/Sumut官方SPKN2026附件链；Yogyakarta另一个166页官方Renstra文件的搜索读取包含正式PBPK3/2025正文、公布和认证，区别于此前排除的Rancangan Akhir。但该入口和原件CLI/Chrome仍拒绝，未取得本地字节，不能登记完成的正文审核或扩展白名单。相同文件名/页数不能当作SHA相同。

DPR新的PerDPR3/2025详情实际可读，制定日2 October 2025、公布日7 November 2025只是详情证据；正常Chrome点击 `UNDUH BERKAS` 到 `2025perdpr003.pdf` 仍Akamai Access Denied。动态query更新时间不是法规公布/更新日期，不为每个query重复尝试。DJPP官网正常Chrome点击e-Pengundangan实际到 `e-pengundangan.peraturan.go.id`、ERR_CONNECTION_CLOSED，未找到独立正式公报原件链。新增原文0，既有16份PDF继续保留；工程报告 `data/reports/indonesia-iab-repair-2026-10-15.md` / `.html` 记录当次失败与恢复范围。

用户解锁Mac后补核Sumut目录及页面实际提供的SPKN2026 PDF，Chrome均显示BPK Access Denied，未取得新原字节。恢复前的工具中断与恢复后的真实网站拒绝分别保留。七个任务Chrome标签逐个核对地址后关闭，原有标签保留，IAB任务标签自动清理；解锁续跑结果追加同一批次，不修改此前中断轮的历史报告。
