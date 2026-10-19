# 选择印尼官方来源

读取工作区实际index.md及脚本ADAPTERS注册表；以运行报告判断当次网络与下载状态，不把已适配当作已连通。逐平台先验证列表→详情→正式原件，不以固定旧PDF替代动态发现。

| 平台 | 选择与来源边界 |
| --- | --- |
| peraturan.go.id | 核对DJPP正式法规详情及原文；草案、审议附件不替代正式正文。 |
| bpk.go.id | 区分综合法规库与本机关JDIH；较窄的机构资料不构成等价全面替代。 |
| jdihn.go.id | 将聚合目录与成员原件分开溯源；只接受适配器已核对的成员白名单，不开放任意*.go.id。 |
| mkri.id | 选择PMK正式法规，排除法院裁决；大附件需先核实大小再显式提高上限。 |
| mahkamahagung.go.id | 选择PERMA；主JDIH不可用时核实同机关Kepaniteraan公开栏目与实际链接。 |
| dpr.go.id | 选择正式PerDPR，区分目录可读与正文服务器拒绝；不以汇编或草案替代选定原文。 |
| ojk.go.id | 当前适配选择POJK，排除FAQ及摘要。 |
| kemendag.go.id | 选择正式peraturan详情及原件。 |
| kemkes.go.id | 对预览器file参数解码后再次验证官方主机。 |
| kemenkeu.go.id | 区分正式正文、摘要与说明文件。 |
| bkpm.go.id | 核对正式法规及已确认的官方存储主机，不接受未知跳转。 |

启用JDIHN实验官方备用入口时显式传`--experimental-sources`。当前白名单限定`dev-jdihn.bphn.go.id`目录/详情及已核实Setneg官方原件路径，保留`experimental_official_fallback`、`production_discovery`及`experimental_success`区分；成功不表示正式站恢复。新增主机必须先核实真实官方链路，不能泛化白名单。

处理草案冲突、扫描件、大PDF及审核更正时读取[indonesia.md](indonesia.md)；处理连接故障或浏览器补收分别读取[网络流程](network-troubleshooting.md)与[浏览器流程](browser-workflow.md)。

仅在追查过去验收时检索[2026年10月平台观察](history/platforms-2026-10.md)，关键词`2026-10-13|2026-10-14|来源和资料边界`。历史样本数量、HTTP状态和浏览器恢复结果只适用于当次证据，不是新环境默认状态。工程reports路径是可选历史证据，独立包不依赖它们运行。
