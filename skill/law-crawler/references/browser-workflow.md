# 浏览器辅助采集与官方文件导入

## 选择当前可用能力

在CLI无法取得官方原文、且已明确原文采集时，使用所在agent当前提供的浏览器工具。读取该工具文档，分别核对连接、标签管理、导航、内容读取及下载；不要求Codex、CUA、Chrome或固定浏览器ID。没有相应工具时保留CLI结果并报告能力缺失，不模拟调用。

先检查已有页面及实际URL，再创建任务标签或复用已正常加载的同站页面。关闭标签时只处理本任务创建或明确归属本任务的标签；保留用户标签、Cookie、浏览器数据与系统网络设置。工具要求手动解锁或登录时记录工具限制，不能记成官网拒绝。

## 分层诊断有界超时

为每个阶段设置有限时限，并分别记录工具外层时限、DOM内部时限及实际错误。导航成功不证明可读，页面可读不证明附件落盘。使用一个健康对照页定位工具故障；当多种读取接口均超时且没有新证据时停止同类重试。仅使用当前公开的替代接口；URL策略或访问控制拒绝后不能换通道绕过。

需要验证连接/读取/下载链路时，启动固定内存诊断服务：

```text
python -X utf8 "SKILL_DIR/scripts/browser_healthcheck.py" --duration 180 --output-dir "WORK_ROOT/reports/browser-healthcheck-RUN_ID"
```

替换路径与唯一RUN_ID。读取ready JSON返回的随机loopback URL和marker，使用浏览器正常读取页面及点击附件，再核对工具返回路径、字节数及SHA。服务只提供合成内存数据，不暴露工作区文件；到期自动关闭，输出目录不可覆盖。requests.jsonl只证明服务响应；诊断附件不是法规，不得入库。工具不允许本地服务时跳过此路线，不通过它代理读取被策略拒绝的内容。

读取慢官网时依据当前证据调整有界时限，不硬编码历史50秒结论。优先正常点击已观察到的附件；支持下载事件时先注册再点击。超时后核对本次页面、已知任务文件及工具返回的路径，不扫描个人其他下载内容，不将点击成功写成下载成功。

## 取得并核对正式原件

核对真实列表或详情中的法规类型、标题、编号和年份，排除裁决、摘要、FAQ、草案或新闻。记录实际官方链接及来源字段；缺证据日期和适用地区按[schema.md](schema.md)保留未知。确认正常下载已产生本地字节，按[file-formats.md](file-formats.md)检查真实格式、大小和SHA。未确认下载终点时保留final_url_verified=false，不将起始链接冒充已验证终点。

将浏览器取得、CLI公开请求取得和离线导入分别记录；只有实际验证官方原件才能计入文件数量。目录可读或本地导入成功都不代表自动发现链路恢复。

## 导入配置目录并核对结果

使用随技能打包的 `scripts/import_browser_download.py`，它不发起网络请求，并复用 `CrawlStore` 的目录锁、来源合并、SHA-256 去重和不同内容版本保留。它不会删除浏览器下载的原文件。每次导入先生成批次计划，保存 manifest 文本快照，解析后生成本地路径为绝对路径的 `manifest-snapshot.json`；重跑使用原快照，失败项重新导入，已成功且本地缓存有效的项复用。离线读取不做网络退避，状态请求次数为 0。

文件实际由正常CLI公开请求取得时使用 `--acquisition-method official_cli_download`，报告为 `official_file_import`、来源为 `official_cli_download`，本次声明保存于 `sources[].official_file_import`；浏览器方式默认不变。计划冻结取得方式，重跑不得变更。目录浏览器可读与原件CLI可达分别记录，不能把CLI取件写成浏览器下载成功。

准备 JSON manifest，可为单个对象或对象数组。每项包含：

- `platform`：国家索引中的平台域名，且必须有适配器。
- `local_file`：实际下载文件路径；相对路径按 manifest 所在目录解析。
- `detail_url`：已观察到的官方详情/列表 URL；缺失时为 `null`。
- `download_url`：页面实际提供的官方文件链接。
- `final_url` 与 `final_url_verified`：未确认终点时省略或置空 `final_url`，并设 `final_url_verified: false`；只有取得证据后才同时填写终点和 `true`。导入记录可能用 `download_url` 作为兼容占位值，仍须保留验证标记为 `false`。
- `metadata`：schema 2 字段对象，至少有 `title`、`published_at`、`updated_at`、`effective_region`、`document_type`；每项包含 `value/raw/source_label/source_url/status/meaning`。标题必须有观察证据，`document_type.value` 通常为 `regulation`，并保留官方法规类型原文；仅最高法院正式 SEMA 可为 `judicial_guidance`。为 SEMA 提供 `status: observed` 的完整 `legal_identity`：`type: "Surat Edaran Mahkamah Agung"`、`issuer: "Mahkamah Agung"`、`jurisdiction: "ID"` 及非空 `number/year`；文种原文填 `Surat Edaran Mahkamah Agung` 或 `SEMA`。标题、文种与身份来源须在 `jdih.mahkamahagung.go.id` 或 `kepaniteraan.mahkamahagung.go.id`，平台填 `mahkamahagung.go.id`。将 SEMA 标注为印度尼西亚最高法院通函/司法指导文件，不等同于中国法的司法解释；不因此接收其他指导文件、法院判决、草案或新闻。不能从文件名猜出这些字段。

所有声明的来源 URL 都须属于对应平台适配器的官方主机白名单；不要临时泛化白名单来接受未知跳转。

```text
python -X utf8 "SKILL_DIR/scripts/import_browser_download.py" --root "WORK_ROOT" --jurisdiction Indonesia --mode content --manifest "MANIFEST_PATH"
```

替换SKILL_DIR、WORK_ROOT、MANIFEST_PATH占位符及示例法域。`--root`下沿用既有索引和下载配置，显式 `--jurisdiction` 须匹配登记索引与文件平台归属。旧 `--country` 为显式法域别名；不能仅通过索引路径猜测用户法域。自定义输入支持 `--index`、`--config`，仍须匹配登记。导入默认上限为40MiB；只在确认实际文件大小后显式提高，拒绝截断文件。

`--rerun-batch <ID>` 可重跑已有浏览器批次，不能另传新的 manifest 或修改法域/模式；`--rerun-all` 可重新处理成功项。

检查配置目录中的原件、每文件 JSON 和本批次 Markdown/HTML/JSON 报告（`browser-import-last-run.json` 是旧入口报告），核对 SHA-256、字节数、来源、未知字段以及错误数。按实际取得方式核对来源和报告：浏览器方式为 `discovery_method: browser_download`、`browser_import`；CLI方式为 `official_cli_download`、`official_file_import`。两者均核对 `final_url_verified`。重复导入应复用相同内容文件；不同字节应保留不同版本。仅实际使用浏览器补收时注明 `browser-assisted`；不要据此将 CLI 的网络失败状态改成全自动成功。

相同 SHA、相同来源重复导入时，本次未知字段不会覆盖已有 `observed` 证据，未知终点不会覆盖已验证终点；本次声明仍保存在 `browser_import`，报告的 `stored_final_url` / `stored_final_url_verified` 表示合并后记录。不同内容或不同来源不继承这些证据。


## 追查历史

仅在比较旧工具行为时按需检索[2026年10月浏览器观察](history/browser-2026-10.md)，关键词`IAB|domSnapshot|timeout|downloadMedia`。不要在其他agent中照抄历史CUA接口，或依赖工程报告文件完成当前任务。
