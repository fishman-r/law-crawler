# 阿里云 Linux 部署：Claude Code + DeepSeek + 只读看板

本部署让 Claude Code 在独立的 `lawcrawler` 用户下运行完整 skill，并用 Python 标准库提供中文观测页。看板只监听 `127.0.0.1:8765`，通过 SSH 隧道访问，每 5 秒刷新。没有网页命令执行入口，也不需要 Docker、Node.js 或前端构建服务。

## 环境与目录

需要可用的 SSH 管理权限、完整 Python 3.10+（含 SSL、SQLite、fcntl）、Bash、curl、CA 证书，以及能访问 Claude 安装源、DeepSeek API 和目标官方法规网站的网络。建议安装 git、tmux。DeepSeek API 必须有可用额度。

[Claude Code 官方要求](https://code.claude.com/docs/en/setup#system-requirements)是 4 GB+ 内存。2 GB 实例可做单会话小任务试运行；安装成功或一次验收通过不能保证长上下文、多代理或长期采集稳定。看板会显示物理内存不足提示。

| 路径 | 用途 |
| --- | --- |
| `/opt/law-crawler/releases/<version>/` | 只读源码版本 |
| `/opt/law-crawler/current` | 当前源码版本的符号链接 |
| `/home/lawcrawler/.claude/skills/download-official-regulations/` | Claude 用户级完整技能包 |
| `/home/lawcrawler/.claude/settings.json` | DeepSeek 配置、密钥和观测 hooks，权限 0600 |
| `/var/lib/law-crawler/workspace/` | 工作区索引、下载路径配置和批次计划 |
| `/var/lib/law-crawler/downloads/` | 法规原件、元数据、状态及执行报告 |
| `/var/lib/law-crawler/runtime/` | 白名单事件、会话摘要和进程身份记录 |

部署不迁移开发机的下载资料或历史批次。每台机器使用自己的明确绝对路径，避免旧计划绑定到其他工作区。`downloads/`、`reports/`、运行状态、环境配置及密钥均不随源码提交。

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
    '/opt/law-crawler/current/tools/configure_server.py',
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

可以在 Claude 中明确输入，例如“使用 download-official-regulations，适用法域加拿大，只采集目录元数据，每个平台最多检查 1 个详情页”。未给出明确法域和采集模式时，skill 会先确认输入；部署验收不自动启动正式法规采集。

在自己的电脑另开一个终端，保持 SSH 隧道运行：

```sh
ssh -N -L 8765:127.0.0.1:8765 root@SERVER_IP
```

打开 [本地观测台](http://127.0.0.1:8765)。若本地 8765 已占用，把命令中第一个 `8765` 换成空闲端口，并使用对应地址。服务器公网不需要放行 8765。

## 状态的含义与边界

- 会话显示最近事件、工具名称、skill 调用和子代理活动。`Stop` 表示本轮回复结束，`SessionEnd` 才表示会话结束；等待审批单独显示。
- `process_alive` 仅在启动器的 PID、boot ID、启动 tick 均能核验时给出布尔值；绕过启动器启动的会话可能显示未知。陈旧事件、进程消失不会继续冒充运行中。
- “技能已调用”只表示已加载技能，不能证明法规已采集成功。批次结果来自工作区真实计划、状态和报告；未完成的持久记录不会被推断成仍有进程运行。
- 没有正式批次时，批次区域保持空白。看板不填入演示采集结果。
- 事件存储仅保留白名单字段，不保存提示词、对话正文、命令内容、工具输入输出或 API Key。批次原因和 URL 经过脱敏；Claude 自身的本地会话记录仍由 Claude 管理，不通过本看板提供。
- 每个会话保留有界近期事件，最多展示 64 个会话；批次和状态列表也有读取上限，详见模块常量及页面提示。

观测使用 [Claude Code hooks](https://code.claude.com/docs/en/hooks)。默认跟踪 `Skill` 工具调用；较新的直接 `/skill-name` 展开事件 `UserPromptExpansion` 需要在确认目标 Claude 版本支持后，用 `CLAUDE_OBSERVER_USER_PROMPT_EXPANSION=1` 运行配置器安装。旧版本不能仅凭 Skill 工具事件完整观察直接 slash 调用。新会话启动后才能读取重新安装的 hooks。

2026-10-16 在 Alibaba Cloud Linux 4 / Python 3.11 / Claude Code 2.1.292 上实测：DeepSeek 请求成功，Skill 工具加载成功，只读子代理完成；直接 `/download-official-regulations` 产生了 `UserPromptExpansion` 事件。本次实例已启用此扩展事件，后续安装或更新同版本时使用 `CLAUDE_OBSERVER_USER_PROMPT_EXPANSION=1 bash deploy/install.sh` 保持启用。

## 运维与更新

```sh
systemctl status law-crawler-observer --no-pager
journalctl -u law-crawler-observer -n 50 --no-pager
curl -fsS http://127.0.0.1:8765/healthz
systemctl restart law-crawler-observer
```

服务开机自启、异常退出自动重启，以只读文件系统视图和 192 MB 内存上限运行。更新时把新源码放到新的 release 目录，再运行其中的 `deploy/install.sh`；安装器切换 `current`，下载目录、已有索引及 API Key 保留。运行中的 Claude 会话应先结束，再更新技能和 hooks。

更新或排障时不要输出完整 `settings.json`，不要将其写入工单、仓库或网页。看板中的“密钥已配置”只表示存在配置值；一次真实 Claude API 请求才能证明认证和额度当前可用。
