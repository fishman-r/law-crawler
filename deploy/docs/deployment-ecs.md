# 阿里云 Linux 部署：Claude Code + DeepSeek + 只读看板

本部署让 Claude Code 在独立的 `lawcrawler` 用户下运行完整 skill，并用 Python 标准库提供中文观测页。看板只监听 `127.0.0.1:8765`，通过 SSH 隧道访问，每 5 秒刷新。没有网页命令执行入口，也不需要 Docker、Node.js 或前端构建服务。

## 环境与目录

需要可用的 SSH 管理权限、完整 Python 3.10+（含 SSL、SQLite、fcntl）、Bash、curl、CA 证书，以及能访问 Claude 安装源、DeepSeek API 和目标官方法规网站的网络。建议安装 git、tmux。DeepSeek API 必须有可用额度。

[Claude Code 官方要求](https://code.claude.com/docs/en/setup#system-requirements)是 4 GB+ 内存。2 GB 实例可做单会话小任务试运行；安装成功或一次验收通过不能保证长上下文、多代理或长期采集稳定。看板会显示物理内存不足提示。

| 路径 | 用途 |
| --- | --- |
| `/opt/law-crawler/releases/<version>/` | 只读源码版本 |
| `/opt/law-crawler/current` | 当前源码版本的符号链接 |
| `/home/lawcrawler/.claude/skills/law-crawler/` | Claude 用户级完整技能包 |
| `/home/lawcrawler/.claude/settings.json` | DeepSeek 配置、密钥和观测 hooks，权限 0600 |
| `/var/lib/law-crawler/workspace/` | 工作区索引、下载路径配置和批次计划 |
| `/var/lib/law-crawler/downloads/` | 法规原件、元数据、状态及执行报告 |
| `/var/lib/law-crawler/runtime/` | 白名单事件、会话摘要和进程身份记录 |

源码工程按`skill/`、`web/`、`deploy/`、`data/`组织；本机`data/`中的资料不随部署上传。服务器持久数据继续使用上表的独立绝对路径，本次目录重排不移动线上数据。运行状态、环境配置及密钥均不随源码提交。

## 安装

将完整源码放入新的 release 目录，不包含 `.git`、开发机下载资料、报告或私有配置。在 Alibaba Cloud Linux 上先安装系统工具：

```sh
dnf install -y python3 curl ca-certificates git tmux
cd /opt/law-crawler/releases/<version>
bash deploy/install.sh
```

安装器建立用户、安装官方原生版 Claude、复制完整技能包和缺失索引、配置 hooks，并启用 `law-crawler-observer.service`。运行用户无 root 权限；启动器默认使用手动工具审批模式，不跳过审批。

根据 [DeepSeek 官方 Claude Code 接入文档](https://api-docs.deepseek.com/guides/anthropic_api/)，连接地址为 `https://api.deepseek.com/anthropic`。本安装器将主模型及 Opus/Sonnet 别名指向 `deepseek-flash[1m]`，Haiku/子代理指向 `deepseek-flash`，防止 Claude 默认别名切换到另一计费模型。模型支持与价格以提供方当时文档及实际响应为准。

首次配置密钥时，以 root 执行以下命令，终端隐藏输入；密钥通过进程标准输入传递，不进入 shell 历史或命令参数：

```sh
python3 - <<'PY'
import getpass, json, subprocess
key = getpass.getpass('DeepSeek API Key: ')
payload = {'env': {
    'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
    'ANTHROPIC_AUTH_TOKEN': key,
}}
subprocess.run([
    'runuser', '-u', 'lawcrawler', '--', 'python3',
    '/opt/law-crawler/current/deploy/tools/configure_server.py',
    '--workspace', '/var/lib/law-crawler/workspace',
    '--download-base', '/var/lib/law-crawler/downloads',
    '--runtime-dir', '/var/lib/law-crawler/runtime',
    '--provider-config-stdin',
], input=json.dumps(payload), text=True, check=True)
PY
```

## 启动 Claude 与访问网页

SSH 登录后运行：

```sh
law-claude
```

`law-claude` 进入持久工作区，并记录 Claude 的 PID、Linux 启动身份和退出状态。root 调用时会自动转为 `lawcrawler` 用户。安装器也在尚无全局 `claude` 时添加别名，但若用户 PATH 优先选中了原生二进制，直接 `claude` 会绕过进程身份记录；因此推荐始终使用 `law-claude`。若要断开 SSH 后继续会话：

```sh
tmux new -A -s law-crawler
law-claude
# Ctrl+B 然后 D，暂时离开 tmux；下次用同一个 tmux 命令返回。
```

可以在 Claude 中明确输入，例如“使用 law-crawler，适用法域加拿大，只采集目录元数据，每个平台最多检查 1 个详情页”。未给出明确法域和采集模式时，skill 会先确认输入；部署验收不自动启动正式法规采集。

在自己的电脑另开一个终端，保持 SSH 隧道运行：

```sh
ssh -N -L 8765:127.0.0.1:8765 root@SERVER_IP
```

打开 [本地观测台](http://127.0.0.1:8765)。若本地 8765 已占用，把命令中第一个 `8765` 换成空闲端口，并使用对应地址。服务器公网不需要放行 8765。

## 下载批次与浏览法规文件

在“采集批次”中选中原文下载批次，点击“打包下载本批次”。浏览器将 ZIP 保存到本机下载目录；压缩包生成需要片刻，请保持 SSH 隧道连接。打包范围来自该批次实际完成报告中的成功文件，包含法规文件、对应元数据和 `manifest.json` 清单。仅采集元数据的批次没有法规原件，打包按钮不可用；最新执行轮次尚未结束时也会暂时禁用。存在采集失败项的批次可导出成功原件，清单会保留失败提示。

“法规文件”提供服务器法规库的资源浏览器：按国家筛选、按标题或文件名搜索、翻页查看，并可下载单个文件及其元数据。选中文件可在页面预览 PDF、栅格图片或 Markdown；转换后的 Markdown 另有“下载原始响应”入口，ZIP 同时保留原始响应与转换文本。浏览器不支持嵌入 PDF 时可使用新窗口预览。HTML 等可执行内容只提供下载。文件列表按需加载，可手动刷新。

资源浏览器只读取下载根目录下、由采集流程登记的法规文件；不会展示任意服务器路径、API Key、Claude 会话、数据库或运行日志。文件访问和批次导出均保留本机 Host 与同源检查。原件的身份审查状态与下载是否成功分别展示，查看或打包文件不会改变法规的审查结论。

ZIP 在服务器临时目录中逐块生成，完成文件完整性校验后再发送，避免将整个压缩包放进内存。一次只允许一个批次打包请求；文件过多、体积超限、文件缺失或校验失败时会返回明确错误。下载和预览不会修改服务器原件。

## 状态的含义与边界

- 会话显示最近事件、工具名称、skill 调用和子代理活动。`Stop` 表示本轮回复结束，`SessionEnd` 才表示会话结束；等待审批单独显示。
- `process_alive` 仅在启动器的 PID、boot ID、启动 tick 均能核验时给出布尔值；绕过启动器启动的会话可能显示未知。陈旧事件、进程消失不会继续冒充运行中。
- “技能已调用”只表示已加载技能，不能证明法规已采集成功。批次结果来自工作区真实计划、状态和报告；未完成的持久记录不会被推断成仍有进程运行。
- 没有正式批次时，批次区域保持空白。看板不填入演示采集结果。
- 事件存储仅保留白名单字段，不保存提示词、对话正文、命令内容、工具输入输出或 API Key。批次原因和 URL 经过脱敏；Claude 自身的本地会话记录仍由 Claude 管理，不通过本看板提供。
- 每个会话保留有界近期事件，最多展示 64 个会话；批次和状态列表也有读取上限，详见模块常量及页面提示。

观测使用 [Claude Code hooks](https://code.claude.com/docs/en/hooks)。默认跟踪 `Skill` 工具调用；较新的直接 `/skill-name` 展开事件 `UserPromptExpansion` 需要在确认目标 Claude 版本支持后，用 `CLAUDE_OBSERVER_USER_PROMPT_EXPANSION=1` 运行配置器安装。旧版本不能仅凭 Skill 工具事件完整观察直接 slash 调用。新会话启动后才能读取重新安装的 hooks。

2026-10-16 在 Alibaba Cloud Linux 4 / Python 3.11 / Claude Code 2.1.292 上实测：DeepSeek 请求成功，Skill 工具加载成功，只读子代理完成；当时直接调用技能产生了 `UserPromptExpansion` 事件。本次实例已启用此扩展事件，后续安装或更新同版本时使用 `CLAUDE_OBSERVER_USER_PROMPT_EXPANSION=1 bash deploy/install.sh` 保持启用。

## 运维与更新

```sh
systemctl status law-crawler-observer --no-pager
journalctl -u law-crawler-observer -n 50 --no-pager
curl -fsS http://127.0.0.1:8765/healthz
systemctl restart law-crawler-observer
```

服务开机自启、异常退出自动重启，以只读文件系统视图和 192 MB 内存上限运行。更新时把新源码放到新的 release 目录，再运行其中的 `deploy/install.sh`；安装器切换 `current`，下载目录、已有索引及 API Key 保留。运行中的 Claude 会话应先结束，再更新技能和 hooks。

更新或排障时不要输出完整 `settings.json`，不要将其写入工单、仓库或网页。看板中的“密钥已配置”只表示存在配置值；一次真实 Claude API 请求才能证明认证和额度当前可用。

## 下载专用服务

仅需下载原件时，部署完整的`skill/law-crawler/`及`deploy/tools/configure_downloader.py`到新的、固定的源码版本目录。直接运行归档脚本，不安装Claude、Ollama或模型服务，不读写提供方凭据，不切换`/opt/law-crawler/current`，不重启已有看板。

为服务器建立独立工作区、明确的绝对`downdloadDir`和新批次。保留本机已停止任务的原件、索引快照、计划、签名和运行记录；现有bulk断点及raw缓存包含冻结的绝对路径，不能直接重写路径和签名来冒充原批次。需要跨路径迁移断点时，另行生成具有来源映射和校验证据的派生批次。

先检查SSH权限、现有下载进程、Python与SSL/SQLite/fcntl、磁盘余量和下载目录。使用配置器的dry-run检查固定源码路径、所选法域、平台范围、批次、目录和systemd单元，再安装配置、重新加载systemd并显式启动。下载使用低权限账户、有界网络重试和服务资源限制；失败保留断点，不自动启动付费模型处理。

例如以服务器root执行下列预览；将`VERSION`、`RUN_ID`替换为实际版本和独立批次，平台参数省略时使用该法域登记来源。确认预览后去掉`--dry-run`安装配置，再运行工具输出的`systemctl`命令：

```sh
python3 /opt/law-crawler/releases/VERSION/deploy/tools/configure_downloader.py --release /opt/law-crawler/releases/VERSION --workspace /var/lib/law-crawler/download-workspaces/RUN_ID --download-base /var/lib/law-crawler/downloads --jurisdiction Indonesia --batch-id RUN_ID --dry-run
```

用`systemd-analyze verify /etc/systemd/system/UNIT.service`验收实际Linux单元后再启动。服务配置为不自动失败重启，停止发送SIGINT；再次`systemctl start UNIT`时会核验冻结计划并自动选择批次重跑。重启不会清除来源失败记录，也不会修改范围或重试上限。

验收时分别检查服务状态、日志增量、实际官方来源响应、原件字节和SHA、元数据、JSON/Markdown/HTML业务报告。归档程序可能在单件失败时仍正常退出，不能用systemd的active或退出0替代下载成功。记录未适配分页、TLS/403、缺附件和待身份核验项。部署包不得包含开发机`data/`、私有配置、密钥、会话和缓存。

## 下载进度接入

为服务器下载批次在`runtime/<deployment>/active-download.json`登记`batch_id`、`state`、`unit`和配置`workers`；`state`必须位于下载根的`<jurisdiction>/pipeline/bulk/<batch>/state.json`。允许观测服务用户遍历该部署目录并读取指针（建议目录`root:lawcrawler 0750`、指针`root:lawcrawler 0640`），不放宽其他私有文件权限。新下载器在`fast-download/status.json`保存轻量摘要；旧批次由`deploy/tools/refresh_download_progress.py --batch-dir ...`生成独立摘要，不改历史计划和状态。

部署原件缺口修复新版时，先把完整 skill 放入新的固定 release，在服务器从工程外运行源码检查与恢复测试；保留旧 release 和原始冻结范围。按[可重放修复流程](../../skill/law-crawler/references/recovery-workflow.md)逐批审核、冻结计划，然后仅切换下载单元的 `PYTHONPATH`、计划路径和独立 `--results-output`。保持 5 路并发及现有沙箱，结果路径放在原批次可写目录内。使用不可覆盖的逐次结果文件时设置 `Restart=no`，避免已写出结果的失败尝试被自动重启反复拒绝；检查原因后以新 attempt 路径显式续跑。每批服务退出后独立回读 OSS 审计，再运行下一批，核对发布器、Web API 与队列同口径；不把审核等待时间显示为正在下载。

候选筛选、冻结计划和执行器共用批次锁。允许审核者并行阅读已经冻结的证据，但把新一轮 `select` 与服务启动错开。若服务因 `FileLockBusy` 在创建 Runner 前退出，保留日志，确认活动 PID 为零、最终 attempt 文件不存在且队列未变后，等待筛选结束，再沿同一计划与尚未使用的 attempt 路径启动；不得删除锁文件或重置队列来绕过互斥。

观测服务仅执行白名单下载单元的`systemctl show`读取状态：有活动PID时显示运行；已停止但无结束标记显示暂停；`finished`显示结束，即使存在下载失败导致服务退出码1。网页分别展示配置并发、本轮并发及当前运行并发上限，不能当作实时活动HTTP请求数。队列100%仅代表已尝试所有已发现详情。

## 将成功原件归档到OSS

使用完整skill中的 `scripts/oss_archive_upload.py`，在目标服务器安装可选依赖 `oss2`。读取已完成批次的 `metadata.json`，只选取 `saved/reused`、完整且HTTP 2xx的文件；排除错误页，按内容SHA去重。上传前核对本地SHA，上传后回读原件与来源元数据核对SHA。默认5路、每项最多3次，保留本地原件；同键已存在但内容不符时报告冲突，不覆盖。

```sh
python oss_archive_upload.py --credentials CREDENTIAL_FILE --metadata BATCH_METADATA_JSON --output UPLOAD_BATCH_DIR --bucket BUCKET --endpoint REGION_ENDPOINT --region REGION --prefix ods/Indonesia --workers 5 --sample
# 样本通过后，以相同参数去掉 --sample 执行整个冻结清单。
```

在工程外创建权限为600的凭据JSON（字段为 `access_key_id`、`access_key_secret`），不要将值写入命令行、代码、报告或Git。临时凭据文件放在服务器受限运行目录，任务完成后移除；需要持续运行时使用独立凭据管理方案。

对象布局为 `ods/Indonesia/raw/<SHA>.<原始扩展名>`、`ods/Indonesia/metadata/<来源批次>/<SHA>.json`、`ods/Indonesia/batches/<上传批次>/{plan,status}.json`。每份元数据保留所有关联来源和身份核验状态；ODS原始归档不等于法规已完成法律审查。状态记录成功、失败、原因、次数、字节数及耗时。重跑同一计划时核验已有对象后跳过上传，不自动删除OSS对象。

### 53,430条冻结目录常驻下载

线上批次 `id-census-oss-20261022` 使用 `law-crawler-census-oss.service`，固定源码release `20261022-census-oss`，5路并发轮转处理七个目录入口（六个平台）。冻结队列、逐详情结果、报告和断点位于服务器 `Indonesia/pipeline/census-download/id-census-oss-20261022/`。只读检查 `systemctl status law-crawler-census-oss.service` 与批次 `status.json`；`systemctl stop` 保存活动任务，`start` 从pending继续。失败/不完整任务保持记录，不因服务重启自动反复重试。

OSS凭据由root在工程外受限配置目录管理，systemd配置只引用路径，不包含密钥。服务开机启动、异常有界重启，OSS鉴权失败或可用空间不足时主动暂停。新文件使用本批临时目录，OSS原件及来源元数据核验后清理；旧原始归档不删除。此服务不自动接入旧批次Web统计，不应将旧批次2,466条详情面板当作本批53,430条进度。
