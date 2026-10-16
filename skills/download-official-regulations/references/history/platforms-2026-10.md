> 保留2026年10月特定环境的历史观察；不将API示例、库存、网络状态或工具超时作为新环境默认事实。文中工程reports路径只用于可选追溯，独立技能包不包含这些报告。执行时回到当前references流程。

# 印尼平台适配与验收

最新增量（2026-10-14）：JDIHN 显式实验备用模式已通过目录→详情→Setneg 原件并验证重复运行；MK 官方档案直链补收成功，自动目录仍受阻。peraturan、BPK、DPR 未恢复。下方为分次历史记录，文件数不是当前国家目录计数；最新操作说明见 [network-troubleshooting.md](../network-troubleshooting.md)。

2026-10-13 实际使用项目配置的 `downdloadDir` 验证：11 个索引平台均有独立适配，6 个通过列表发现到原件保存，5 个受网络或正文服务器限制，尚未通过完整验收。未进行全量翻页；一份样本成功不代表平台全部资料都能采集。实时状态以运行报告为准。

| 索引平台 | 实际入口与范围 | 本次结果 |
| --- | --- | --- |
| `peraturan.go.id` | `https://peraturan.go.id/`，`/id/` 详情及法规 PDF | 主域名与 www 均 TLS EOF，未下载。 |
| `bpk.go.id` | `https://peraturan.bpk.go.id/`，`/Details/` → `/Download/` | HTTP 403，未下载。 |
| `jdihn.go.id` | `https://jdihn.go.id/pencarian`，索引详情与明确的成员原件链接 | TLS EOF，未下载。 |
| `mkri.id` | `https://www.mkri.id/peraturan/peraturan-mk`，PMK 正式法规列表 | 列表与 JDIH 备用入口 HTTP 403；已单文件补收 PMK 2/2023 原件并验证去重，但自动发现链路仍未通过。 |
| `mahkamahagung.go.id` | JDIH PERMA 列表；同机关 Kepaniteraan 的 PERMA 栏目作备用 | 通过备用公开栏目取得 PERMA 2/2015 正文。 |
| `dpr.go.id` | `jdih.dpr.go.id` PerDPR 列表、详情的 `UNDUH BERKAS` | 本次主流程列表/详情可解析；`berkas.dpr.go.id` 的 3 份正文均 HTTP 403。 |
| `ojk.go.id` | `https://www.ojk.go.id/id/regulasi/Default.aspx`，POJK | 通过，POJK 11/2026。 |
| `kemendag.go.id` | `https://jdih.kemendag.go.id/`，正式 `peraturan-*` | 通过，已有 Permendag 25/2026 等文件可复用。 |
| `kemkes.go.id` | `https://jdih.kemkes.go.id/`，法规详情与 PDF 预览器 file 参数 | 通过，Permenkes 6/2026。 |
| `kemenkeu.go.id` | `https://jdih.kemenkeu.go.id/home`，`/dok/` 法规详情 | 通过，PMK 74/2026。 |
| `bkpm.go.id` | `https://jdih.bkpm.go.id/id`，法规详情与官方存储域名 | 通过，Permen 6/2026。 |

## 2026-10-14 浏览器补充验收

以上表格为 10 月 13 日 CLI/网络记录。本次改用可用的内置浏览器 IAB：MK 官方法规目录实际列出 PMK 1/2026，点击真实下载链接取得 74,168,413 字节 PDF，导入配置目录并验证 SHA-256 与重复导入；该平台通过 **browser-assisted** 样本链路，CLI 列表请求仍 403。

DPR 新官网目录与下载对话框可读，但 3/2025、1/2024 两份 PDF 均显示 Access Denied；BPK 主入口和 JDIH 备用入口显示 Access Denied；peraturan/JDIHN 导航超时后显示不可达标题。四个平台仍未完成原件下载。Chrome 扩展通道仍超时；IAB 可用不能等同于 Chrome 已修复。

本地现有 10 份 PDF；6 个平台 CLI 样本成功，加上 MK 浏览器辅助成功为 7 个，另 4 个未通过。浏览器导入不修改 CLI 的失败状态；其独立报告为 `browser-import-last-run.json`。方法见 [browser-workflow.md](../browser-workflow.md)。

## 来源和资料边界

- MA 主 JDIH 当前返回 403，但 `kepaniteraan.mahkamahagung.go.id` 是同机关可访问的 PERMA 栏目；JSON 保存实际详情及文件地址。备用栏目未明确列出的公布、更新或适用地区不补猜。
- MK 选择 PMK 法规，不采裁决；DPR 选择正式 PerDPR，不用草案、摘要、Katalog 代替正文。OJK 目前只选择 POJK，避免把 FAQ、摘要或其他栏目混入。
- JDIHN 本身是聚合索引。正式索引成员文件白名单包括在公开详情中确认过的 `jdih.kemendag.go.id`、`jdih.dephub.go.id`、`jdih.jatengprov.go.id`；遇到其它成员需先确认实际来源再扩展适配，不能开放任意 `*.go.id`。显式实验备用模式另允许已核实的 Setneg 原件接口。
- 卫生部预览器的 `file` 参数需解码并再次核对官方主机；财政部和投资部正文、摘要必须区分。投资部允许官方文件主机 `jdih-storage.bkpm.go.id`。
- 官网笼统的 `Tanggal`、网页发布日期、生效日或文件名中的年份不自动当作法规公布日。

## 2026-10-13 历史失败说明

当前 peraturan/JDIHN 为 TLS 连接错误，BPK/MK 为 HTTP 403，DPR 正文为 HTTP 403。正常浏览器复核也因本机浏览器工具连接不可用而未取得页面，未绕过挑战或关闭 TLS。

本日后续专项排查：peraturan/JDIHN DNS 正常且无 AAAA，正常代理/TLS 1.2/SOCKS 请求均未完成 TLS，绕过显式代理的请求 TCP 超时；不能判定具体远端或路由根因。BPK 的 JDIH 及主站法律依据栏目也 403。MK 旧栏目、JDIH 与 PPID 仍拒绝访问。DPR 正常会话附准确 Referer 后 PDF 仍被 Akamai 拒绝；新官网目录仅得到客户端页面，旧 PDF 链接实际返回 HTML。

MK 补收 9,189,211 字节的 PMK 2/2023 后，本地共 9 份 PDF；6 个平台完整链路通过、MK 仅新增独立原件，另外 4 个没有新增原件。补收无详情来源不会参与 `--refresh-metadata`；平台自动发现状态仍保留失败。故障分层与补收约定见 [network-troubleshooting.md](../network-troubleshooting.md)。

国家平台和部分机构的离线解析样本显式标注 synthetic；它们只验证筛选和解析逻辑，不构成真实采集成功证据。网络恢复后重新执行逐平台命令，只有实际 PDF 保存、校验与元数据来源完整时才能更新该平台的验收结论。

## 同日后续四站复核与格式更新

继续检查 DJPP/e-pengundangan/BPHN、BPK E-PPID/地区站、DPR PPID/图书馆及真实附件，仍未取得新正式原件。DJPP 可读附件为草案/审议资料，未入库；本机 TLS/403 持续，浏览器随后也出现连接超时。现有原件仍为 10 份 PDF，剩余 4 平台未通过，不能把搜索服务解析视为本地文件下载。

采集和浏览器导入现已共用多格式识别与去重，支持范围和离线/在线验收区别见 [file-formats.md](../file-formats.md)。工程完整记录为 `reports/four-platforms-formats-2026-10-14.md`。
