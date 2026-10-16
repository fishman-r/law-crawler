# 印尼剩余三平台与浏览器超时排查 · 2026-10-14

> 本报告记录本次执行结果；下载成功不代表法律效力已核验。

## 执行信息

| 项目 | 值 |
| --- | --- |
| 适用国家/法域 | Indonesia |
| 采集模式 | recovery\_verification |
| 执行结果 | blocked\_no\_new\_originals |
| 保存目录 | /Users/rpy/home/github/law-crawler/downloads/Indonesia |
| 浏览器结果 | 已找到并实际验证本机Chrome原生AX与截图替代通道。IAB标签管理/导航可用，但健康对照页DOM、截图均超时；关闭旧大PDF未修复。不能称IAB本身已恢复，根因未确定。 |
| 网站结果 | 真实Chrome页面显示BPK Riau与E-PPID Access Denied；DPR印尼语正式详情可读但正常点击下载到Akamai拒绝页；peraturan浏览器ERR\_CONNECTION\_CLOSED。工具读取故障和网站拒绝分别记录。 |
| 原文及库存 | 本轮未取得合格原文，没有创建新成功文件元数据、没有执行新原件导入重跑。现有16份PDF/元数据/正文审核均可核对，原始SHA一致；不以目录可读、搜索读取或历史汇编冒充下载。既有去重与批次逻辑未改动。 |
| 最新日期边界 | DPR当前印尼语数字详情公布日25 February 2025，旧英语详情26 February 2025。逐来源差异已保存为诊断证据；原件受阻，未合并成唯一已核实日期或修改既有候选/文件记录。 |
| 官方备用范围 | BPK JDIH成员导航→Riau SPKN2026页/PDF，以及E-PPID→明确Salinan PBPK1/2026，均实际403。DPR PPID旧导航HTTP200但内容404，不算目录。DJPP官网法规导航仍指受阻peraturan站，未发现独立正式源。 |
| 下一次有效恢复条件 | 需目标TLS端点/路径恢复可达、官方提供可访问的同身份原件公开入口，或当前拒绝站允许正常公开访问。页面Country字段不能独立证明地域封锁。相同故障没有新证据时停止重复矩阵请求。 |
| 批次编号 | indonesia-three-recovery-20261014 |
| skill更新 | 分层验证连接/标签/导航/内容/下载；IAB读取失败时使用已提供的原生浏览器通道；不解析data错误页；清理仅限任务标签；可读拒绝页不计成功；保留日期冲突与失败条件。 |
| 分工 | 主代理负责IAB与原生Chrome分层排障、真实页面核对、库存校验、报告及工程skill；three\_official\_routes网络子代理负责8次有界官方入口请求与正式附件链。 |
| 证据文件 | \["/Users/rpy/home/github/law-crawler/reports/three-official-routes-2026-10-14.json", "/Users/rpy/home/github/law-crawler/reports/browser-timeout-layer-audit-2026-10-14.json"\] |
| 检查结果 | 196项现有回归通过；skill结构与sync\_skill --check通过；工程skill及本机安装副本一致。 |

## 数量汇总

| 状态 | 数量 |
| --- | --- |
| 新原文 | 0 |
| 新文件元数据 | 0 |
| 现有完整PDF | 16 |
| CLI新路线请求 | 8 |
| 原生浏览器可用通道 | 1 |
| 仍受阻平台 | 3 |

## 法规目录与元数据

无记录。

## 逐文件执行状态

| stage | 执行结果 | elapsed\_seconds | note | timeout\_seconds |
| --- | --- | --- | --- | --- |
| IAB绑定与tabs.list/new | success | 0.382 |  |  |
| 关闭本任务旧大PDF标签6 | success |  | 未清空用户标签、Cookie或浏览器数据 |  |
| 健康对照kemkes正常goto | success | 17.1 |  |  |
| 健康对照DOM读取 | tool\_timeout |  | 同一调用后续BPK/DPR读取尚未执行，不计它们的页面结果 | 25 |
| IAB健康页screenshot | tool\_timeout |  |  | 20 |
| 原生Chrome getApp/AX | success | 0.758 |  |  |
| 原生Chrome截图 | success |  | 真实截图显示BPK E-PPID Access Denied；未点击Show IP |  |
| 清理本次Chrome五个标签 | success |  | 核对每次当前地址，只关闭本次创建的标签；已恢复用户原有报告标签 |  |

## 下载文件

无记录。

## 错误与原因

| 官方平台 | 失败原因 | 原件来源 | 请求次数 | request\_status | 原因 | component |
| --- | --- | --- | --- | --- | --- | --- |
| peraturan.go.id | 浏览器连接被关闭；既有TLS EOF仍未解决 |  |  |  |  |  |
| bpk.go.id | 新的官方入口与原件403/Access Denied |  |  |  |  |  |
| dpr.go.id | 正常下载到Akamai拒绝页，未取得原字节 | https://berkas.dpr.go.id/jdih/document/peraturan\_dpr/2025perdpr001.pdf?t=20261014223941 | 1 | browser\_access\_denied\_page | 正常点击UNDUH BERKAS后实际拒绝，未产生可接受本地原文；403类型不做重复重试 |  |
|  | 健康对照DOM25秒、截图20秒超时；原生Chrome替代可用 |  |  |  |  | IAB内容读取 |

## 平台结果

| 官方平台 | 状态 | browser\_result | 原因 |
| --- | --- | --- | --- |
| peraturan.go.id | blocked | ERR\_CONNECTION\_CLOSED | 未确认新独立正式原件源 |
| bpk.go.id | blocked | Riau/E-PPID均Access Denied | 已确认官方附件链，本机CLI403，正常浏览器页面也拒绝 |
| dpr.go.id | original\_download\_blocked | 正式详情可读；正常点击原件仍Akamai Access Denied | 当前2025原件未取得；元数据存在公布日差异 |

